"""BRIO's canonical contact details for anything client-facing.

Single source of truth, mirroring `brio-website/lib/constants.ts::COMPANY` (which
matches the live builtbybrio.com) and `brio-pm/frontend/src/lib/company.ts`.

Read from here — never re-type these into a template. The phone number changed to
1 (236) 706-9686 and the old 1 (604) 260-6691 survived in nine hard-coded places
across the invoice, estimate and scope PDF generators, so every client document
this system produced carried a number that reaches nobody. `internal-ops` had the
identical failure: a brand.json that nothing read.
"""

COMPANY = {
    "name": "BRIO Construction",
    "legal_name": "BRIO Construction Inc.",
    "phone": "1 (236) 706-9686",
    "phone_clean": "+12367069686",
    "email": "info@builtbybrio.com",
    "website": "builtbybrio.com",
    "website_www": "www.builtbybrio.com",
    "address": "267 Esplanade W, North Vancouver BC V7M 1A5",
    "gst_number": "000000000RT0001",
}

# Pre-composed blocks, so a template never rebuilds them slightly differently.
LETTERHEAD_HTML = (
    f'{COMPANY["legal_name"]}<br>{COMPANY["website_www"]}<br>\n'
    f'    {COMPANY["email"]}<br>{COMPANY["phone"]}<br>GST#: {COMPANY["gst_number"]}'
)

FOOTER_CONTACT = f'{COMPANY["email"]} &bull; {COMPANY["phone"]}'

# Bare aliases for use inside HTML/CSS f-string templates. Quote-free on purpose:
# a template delimited by " cannot contain COMPANY["legal_name"] without escaping,
# and that friction is what drives people to re-type the literal instead.
LEGAL_NAME = COMPANY["legal_name"]
WEBSITE = COMPANY["website"]
WEBSITE_WWW = COMPANY["website_www"]
EMAIL = COMPANY["email"]
PHONE = COMPANY["phone"]
GST_NUMBER = COMPANY["gst_number"]
