// BRIO's canonical contact details, mirroring brio-website/lib/constants.ts::COMPANY
// (the source of truth that matches the live builtbybrio.com).
// Never hard-code these into a page — a client-facing document that drifts out of
// date sends people to a phone number that reaches nobody.
export const COMPANY = {
  name: "BRIO Construction",
  legalName: "BRIO Construction Inc.",
  phone: "1 (236) 706-9686",
  phoneClean: "+12367069686",
  email: "info@builtbybrio.com",
  website: "https://builtbybrio.com",
  address: "267 Esplanade W, North Vancouver BC V7M 1A5",
} as const;
