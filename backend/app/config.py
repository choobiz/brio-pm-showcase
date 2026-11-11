from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = "postgresql+psycopg://brio:brio@localhost:5434/brio_pm"
    nextauth_secret: str = "change-me"

    anthropic_api_key: str = ""

    # Prepared documents (phase 9 part 2). The Mac worker authenticates with
    # this shared secret; empty = the worker endpoints are closed.
    prep_worker_token: str = ""
    # Only these accounts may raise the monthly API cap (Amir, 2026-10-13).
    prep_cap_owner_emails_raw: str = Field(
        default="amir@builtbybrio.com,amir@sister-co.example", alias="PREP_CAP_OWNER_EMAILS")

    @property
    def prep_cap_owner_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.prep_cap_owner_emails_raw.split(",") if e.strip()}

    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "brio-pm-dev"
    r2_endpoint: str = ""

    aws_ses_access_key_id: str = ""
    aws_ses_secret_access_key: str = ""
    aws_ses_region: str = "us-west-2"
    aws_ses_from_email: str = ""
    #: Domains verified in SES (with DKIM): a user signed in on one sends as
    #: themselves; anyone else sends "Name via Keystone" from aws_ses_from_email.
    ses_sender_domains: str = "builtbybrio.com"

    cors_origins: str = "http://localhost:3000"

    # Comma-separated list of email addresses allowed to sign in.
    # Empty = no allowlist enforced (only the dev stub fallback applies).
    auth_allowed_emails_raw: str = Field(default="", alias="AUTH_ALLOWED_EMAILS")

    # Track 2 Phase 6: when true, the estimate worker runs the rate-library
    # reconciliation post-pass (rate × takeoff qty) after the LLM prices the
    # estimate. Default off — kill switch for the new deterministic pricing.
    use_rate_library: bool = Field(default=False, alias="USE_RATE_LIBRARY")

    # Review solicitation engine — Google Places (New) for the review count + the
    # "write a review" link. brio_place_id defaults to BRIO Construction (North
    # Van); the key must be supplied via env (never committed).
    google_places_api_key: str = Field(default="", alias="GOOGLE_PLACES_API_KEY")
    brio_place_id: str = Field(default="ChIJcWE-nMZxhlQR73vRsWwlHGc", alias="BRIO_PLACE_ID")
    # Where the daily review-ask reminder goes (comma-separated).
    review_reminder_emails: str = Field(default="", alias="REVIEW_REMINDER_EMAILS")

    # Inbound-email ingestion (Conversations P2). The Cloudflare Email Worker
    # authenticates to POST /inbound/email with this shared secret; the domain
    # is used to build each project's per-project BCC address.
    inbound_webhook_secret: str = Field(default="", alias="INBOUND_WEBHOOK_SECRET")
    inbound_email_domain: str = Field(default="inbound.example.com", alias="INBOUND_EMAIL_DOMAIN")

    # Business-card intake (builtbybrio.com/card/<slug>). The website's server
    # route sends this in the request BODY (the proxy drops unknown headers);
    # empty = POST /public/card-leads refuses everything.
    card_intake_key: str = Field(default="", alias="CARD_INTAKE_KEY")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_allowed_emails(self) -> list[str]:
        return [e.strip().lower() for e in self.auth_allowed_emails_raw.split(",") if e.strip()]


settings = Settings()
