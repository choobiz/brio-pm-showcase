import NextAuth from "next-auth";
import Nodemailer from "next-auth/providers/nodemailer";
import PostgresAdapter from "@auth/pg-adapter";
import { Pool } from "pg";

// Singleton pool — Vercel re-uses the function process so module-scope state
// survives across requests. SSL is required for the nip.io-fronted Postgres
// (Cloudflare terminates TLS at the edge; the actual connection from Vercel
// goes directly to the VM's exposed port, so SSL is on the wire).
declare global {
  // eslint-disable-next-line no-var
  var __brioPgPool: Pool | undefined;
}
/**
 * How this connection is protected.
 *
 * The database is reached over the public internet, because serverless
 * functions have no fixed egress IP to allowlist. A password alone is not
 * enough protection for that.
 *
 * So: TLS with a CLIENT CERTIFICATE. Postgres rejects anything without a valid
 * certificate during the TLS handshake, before a password is ever offered,
 * which ends that entire class of attack at the network layer and demotes the
 * password to a second factor.
 *
 * Degrades deliberately rather than failing shut. If the certificate variables
 * are absent the pool still connects over TLS, so a missing env var cannot lock
 * everyone out of the app — it only loses the client-certificate layer, and the
 * server will refuse it once pg_hba requires one. `node-postgres` takes PEM
 * strings inline, so nothing has to be written to disk.
 */
function sslConfig() {
  if (process.env.AUTH_DATABASE_SSL !== "1") return false;
  const ca = process.env.AUTH_DB_CA;
  const cert = process.env.AUTH_DB_CERT;
  const key = process.env.AUTH_DB_KEY;
  if (!(ca && cert && key)) return { rejectUnauthorized: false };

  // The server is verified too, and doing so takes one workaround.
  //
  // `pg` hands Node the string "localhost" as the name to check rather than the
  // host it actually dialled, so the default identity check fails against a
  // certificate that correctly carries the VM's address. SNI cannot carry an IP
  // (Node refuses it, per RFC 6066), so the name cannot be supplied that way
  // either. Instead run Node's own check against the host from the connection
  // string, which matches the IP and DNS SANs on the certificate.
  //
  // This is verification, not a bypass: the chain is still validated against our
  // CA, and the peer must still match the address we meant to reach.
  let host: string | undefined;
  try {
    host = new URL(process.env.AUTH_DATABASE_URL ?? "").hostname || undefined;
  } catch {
    host = undefined;
  }

  return {
    ca,
    cert,
    key,
    rejectUnauthorized: true,
    ...(host
      ? {
          // Deliberately NOT node:tls.checkServerIdentity. This module is
          // imported by src/proxy.ts, which runs on the EDGE runtime, where any
          // `node:*` import breaks the whole auth config. The symptom is subtle
          // and cost real time: the Node-runtime API routes keep working, so
          // curl against /api/auth succeeds, while every BROWSER page
          // navigation 500s. Do the SAN check by hand instead.
          checkServerIdentity: (
            _ignored: string,
            peer: { subjectaltname?: string },
          ) => {
            const names = (peer.subjectaltname ?? "")
              .split(",")
              .map((n) => n.trim());
            return names.includes(`IP Address:${host}`) ||
              names.includes(`DNS:${host}`)
              ? undefined
              : new Error(
                  `server certificate does not name ${host} (SANs: ${names.join(", ") || "none"})`,
                );
          },
        }
      : {}),
  };
}

const pool =
  globalThis.__brioPgPool ??
  new Pool({
    connectionString: process.env.AUTH_DATABASE_URL,
    ssl: sslConfig(),
    max: 3,
  });
if (!globalThis.__brioPgPool) globalThis.__brioPgPool = pool;

/**
 * DB-driven allowlist + role. The `users` table IS the allowlist: a row with
 * active=true may sign in, and its `role` (superadmin/manager/worker/owner)
 * drives the session. Superadmin provisions users via /admin/users — no env
 * allowlist, no redeploy. (Replaces the old AUTH_ALLOWED_EMAILS env.)
 */
async function dbUser(
  email: string | null | undefined,
): Promise<{ active: boolean; role: string } | null> {
  if (!email) return null;
  try {
    const { rows } = await pool.query(
      "SELECT role, active FROM users WHERE lower(email) = lower($1) LIMIT 1",
      [email],
    );
    if (!rows.length) return null;
    return { active: rows[0].active === true, role: String(rows[0].role) };
  } catch {
    return null; // DB unreachable → deny (fail closed)
  }
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  adapter: PostgresAdapter(pool),
  providers: [
    Nodemailer({
      server: {
        host: process.env.EMAIL_SERVER_HOST,
        port: Number(process.env.EMAIL_SERVER_PORT ?? 587),
        auth: {
          user: process.env.EMAIL_SERVER_USER,
          pass: process.env.EMAIL_SERVER_PASSWORD,
        },
      },
      from: process.env.EMAIL_FROM,
    }),
  ],
  // Adapter is for verification-token + user persistence (required by the
  // Email provider). We keep `session.strategy = "jwt"` so the backend can
  // decrypt the JWE cookie via NEXTAUTH_SECRET — DB-session strategy would
  // require the backend to look up the opaque session token instead.
  session: { strategy: "jwt" },
  pages: {
    signIn: "/sign-in",
    verifyRequest: "/auth/verify",
    error: "/sign-in",
  },
  callbacks: {
    /**
     * Gate: only provisioned + active users may sign in (checked against the
     * DB before the magic link is sent). Returning false routes the user to
     * /sign-in?error=AccessDenied.
     */
    async signIn({ user }) {
      const u = await dbUser(user?.email);
      return !!u && u.active;
    },
    /**
     * Pin email + the DB role onto the JWT. Re-derived each turn so a role
     * change by a superadmin takes effect without re-login.
     */
    async jwt({ token, user }) {
      if (user?.email) token.email = user.email;
      if (typeof token.email === "string") {
        const u = await dbUser(token.email);
        // Only update on a successful lookup; a transient DB error must not
        // silently downgrade the role (the backend re-checks the DB anyway).
        if (u) token.role = u.role;
        else if (!token.role) token.role = "worker";
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as { role?: string }).role =
          (token.role as string | undefined) ?? "worker";
        if (typeof token.sub === "string") {
          (session.user as { id?: string }).id = token.sub;
        }
      }
      return session;
    },
  },
});
