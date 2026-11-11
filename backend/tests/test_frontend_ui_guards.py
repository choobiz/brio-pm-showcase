"""Guards on the frontend source for UI bugs found in testing.

CI runs only the backend suite, so frontend regressions that a source scan can
catch live here (Amir, 2026-10-14: every bug found gets a test that keeps it
from coming back).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _tsx_files():
    return [p for p in SRC.rglob("*.tsx") if ".next" not in p.parts]


def _input_tags(text: str):
    """Each `<input ... />` with its line number. Scans to the closing `/>`
    because handlers like `(e) => ...` contain `>`."""
    for m in re.finditer(r"<input\b", text):
        end = text.find("/>", m.start())
        yield text.count("\n", 0, m.start()) + 1, text[m.start():end]


def _class(tag: str) -> str:
    m = re.search(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', tag)
    return (m.group(1) or m.group(2) or "") if m else ""


def test_flex_inputs_can_shrink():
    """Rollout test 2026-10-14: on Scope, the describe-a-change box kept its
    built-in minimum width, pushing "Propose changes" under the document panel,
    where a click landed on the document and nothing was sent. An input that
    grows with `flex-1` must also be allowed to shrink (`min-w-0`)."""
    bad = []
    for path in _tsx_files():
        for line, tag in _input_tags(path.read_text()):
            cls = _class(tag)
            if re.search(r"(^|\s)flex-1(\s|$)", cls) and "min-w-0" not in cls:
                bad.append(f"{path.relative_to(SRC)}:{line}")
    assert bad == [], f"flex-1 inputs without min-w-0: {bad}"


def test_describe_a_change_row_wraps():
    """The same row must wrap in a narrow column, so the button drops below
    the box instead of overflowing it."""
    src = (SRC / "components" / "EditWithPrompt.tsx").read_text()
    row = src[: src.index("value={instruction}")]
    assert "flex flex-wrap gap-2" in row[row.rindex("<div"):], "the Propose row no longer wraps"


def test_change_order_columns_say_what_they_hold():
    """Rollout test 2026-10-14: the Money change-order table's "Days" column
    (days the order adds to the schedule) read as days waiting, and its amounts
    (before GST) sat under a summary tile that includes GST."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "money" / "page.tsx").read_text()
    assert ">Days<" not in src
    assert "Adds to schedule" in src and "Amount before GST" in src


def test_full_daily_report_shows_what_the_write_up_saved():
    """Rollout test 2026-10-14: the full report page dropped the photos and the
    items raised for the office, and showed "None." for sections the write-up
    recorded as not mentioned."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "daily-reports" / "[rid]" / "page.tsx").read_text()
    assert "raised_for_office" in src and "PhotoThumb" in src and "not_recorded" in src
    assert "Not mentioned, so left blank" in src


def test_site_summary_never_prints_none_for_an_unsaid_section():
    """Rollout test 2026-10-14: the Site summary printed "Delays: None." for a
    section the write-up recorded as not mentioned."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "site" / "page.tsx").read_text()
    assert 'return unsaid ? "" : "None."' in src
    assert "unsaid.includes(k)" in src


def test_the_menu_starts_collapsed_on_a_phone():
    """Rollout test 2026-10-14: at 400 px the menu stayed open (a saved desktop
    preference) and took half the screen."""
    src = (SRC / "components" / "AppSidebar.tsx").read_text()
    assert "max-width: 767px" in src and "if (narrow())" in src


def test_no_lib_puts_a_raw_response_body_in_an_error():
    """Rollout test 2026-10-14: the Scope page showed "API 502: <html> <head>
    <title>502 Bad Gateway…" because lib/documentSelection.ts threw the raw
    body. Every lib now fails through lib/apiError.ts."""
    bad = []
    for path in (SRC / "lib").glob("*.ts"):
        if path.name in ("workspaceApi.ts", "apiError.ts"):  # the two careful readers (its comment quotes the old line)
            continue
        text = path.read_text()
        if re.search(r"throw new Error\([^)]*\.text\(\)", text, re.S) or re.search(
                r"`API \$\{[a-z]+\.status\}[^`]*\$\{body\}", text):
            bad.append(path.name)
    assert bad == [], f"libs that show a raw response body: {bad}"


def test_the_document_selection_saves_once_per_change():
    """Rollout test 2026-10-14: the Scope page sent 42 selection saves in a few
    minutes; each save's reply re-armed the debounce because the changed flag
    was never cleared."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "scope" / "DocumentSelector.tsx").read_text()
    effect = src[src.index("if (!dirty.current) return;"):]
    effect = effect[: effect.index("}, [scope, qty, save]);")]
    assert effect.index("dirty.current = false") < effect.index("save(scope, qty)")


def test_allowances_are_not_described_as_having_no_drawings():
    """Rollout test 2026-10-14: 1234's estimate said foundation and site work
    had "no drawings", though its foundation survey and structural plans were
    uploaded (as PDFs). Allowance means not measured from CAD, not no drawings."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "estimate" / "page.tsx").read_text()
    assert "(no drawings)" not in src
    from app.services.drawing import set_completeness
    text = Path(set_completeness.__file__).read_text()
    assert "in the analysed drawing set" in text


def test_a_stray_click_never_discards_a_typed_scope():
    """Rollout test 2026-10-14: clicking outside the Generate dialog closed it
    and lost the scope text typed into it."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "scope" / "page.tsx").read_text()
    assert "onClick={busy || notes.trim() || files.length ? undefined : onClose}" in src
    assert "Ticked below" not in src


def test_an_empty_section_is_still_not_recorded_after_a_write_up():
    """Rollout test 2026-10-14: a new report starts with visitors: [], delays: [],
    safety: {}; the merge treated those as recorded and dropped them from
    not_recorded, so they printed as "None." and the safety warning was lost."""
    src = (SRC / "lib" / "workspaceDailyCapture.ts").read_text()
    assert "!hasContent((current as Record<string, unknown>)[s])" in src
    assert "!(current as Record<string, unknown>)[s]" not in src


def test_the_assistant_is_called_keystone_not_rio():
    """Amir, 2026-10-15: the Scope dialog said "Generate with Rio" while the app
    is Keystone and its assistant is "Ask"."""
    bad = [f"{p.relative_to(SRC)}" for p in SRC.rglob("*.ts*")
           if ".next" not in p.parts and re.search(r"\bRio\b", p.read_text())]
    assert bad == [], f"still says Rio: {bad}"


def test_scope_estimate_and_describe_a_change_show_their_cost_first():
    """Amir, 2026-10-15: like Ask and Prepare, these show an up-to cost on the
    button that starts them, send it as the approved amount, and stay disabled
    until the cost is known."""
    for rel, kind in [
        ("app/(authed)/projects/[id]/scope/page.tsx", '"scope_analysis"'),
        ("app/(authed)/projects/[id]/workbench/estimates/GenerateEstimateButtons.tsx", "KIND"),
        ("components/EditWithPrompt.tsx", '"ai_edit"'),
    ]:
        src = (SRC / rel).read_text()
        assert f"useJobCost(projectId, {kind})" in src, rel
        assert "upTo(cost)" in src and "cost === null" in src, rel
    assert "approve_usd: approveUsd" in (SRC / "lib" / "aiEdits.ts").read_text()


def test_users_and_roles_can_change_a_persons_projects():
    """Rollout decision d3 (2026-10-15): projects could be picked at invite but
    never changed afterwards."""
    src = (SRC / "app" / "(authed)" / "admin" / "users" / "page.tsx").read_text()
    assert "Edit projects" in src and "project_ids: editing.projects" in src


def test_browser_libs_call_the_api_through_the_proxy():
    """Rollout test 2026-10-15: lib/clients.ts called the API host directly with
    credentials: "include"; the session cookie never arrived, so the Clients page
    showed "API 401: not authenticated" to everyone. A lib may name the API host
    only if it runs on the server and forwards the cookie (next/headers)."""
    bad = [p.name for p in (SRC / "lib").glob("*.ts")
           if "NEXT_PUBLIC_API_BASE" in (t := p.read_text()) and "next/headers" not in t]
    assert bad == [], f"browser libs bypassing /api/backend: {bad}"


def test_clients_sales_skills_and_reviews_are_superadmin_menu_items():
    """Amir, 2026-10-15: a manager's menu must not offer them."""
    src = (SRC / "app" / "(authed)" / "layout.tsx").read_text()
    for href in ("/clients", "/crm", "/skills", "/reviews", "/admin/users"):
        at = src.index(f'href: "{href}"')
        assert src.rfind("...(isSuper", 0, at) > src.rfind("]", 0, at) or \
            src.rfind("...(isSuper", 0, at) > src.rfind(": [])", 0, at), f"{href} is shown to managers"


def test_an_edit_with_nothing_proposed_offers_no_save():
    """Rollout test 2026-10-15: "Save as a new version with 0 changes"."""
    src = (SRC / "components" / "EditWithPrompt.tsx").read_text()
    at = src.index("Save as a new version with")
    assert src.rfind("plan.changes.length > 0 && (", 0, at) > src.rfind("pt-0.5", 0, at)


def test_a_project_someone_cannot_open_gets_one_clear_page():
    """Rollout test 2026-10-15, manager on 1234: "Something went wrong" on
    Overview and "no access" under the full tab bar on Money."""
    layout = (SRC / "app" / "(authed)" / "projects" / "[id]" / "layout.tsx").read_text()
    gate = (SRC / "app" / "(authed)" / "projects" / "[id]" / "ProjectGate.tsx").read_text()
    assert layout.index("<ProjectGate") < layout.index("<ProjectTabs")
    assert "You don't have access to this project" in gate and "r.status === 403" in gate


def test_a_signed_takeoff_does_not_say_before_signing_off():
    """Rollout test 2026-10-15: the gaps list kept its "Before signing off:"
    heading on a takeoff that was already signed."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "takeoff" / "page.tsx").read_text()
    # Since 2026-10-20 the list is collapsed behind a one-line summary that
    # carries the count ("Signed off with 6 gaps in the drawing set").
    assert 'pack.status === "approved" ? "Signed off with" : "Before signing off:"' in src


def test_every_estimate_save_path_asks_about_the_rate_library():
    """Phase 3 (Amir, 2026-10-16): fixing an estimate always offers to update the
    library. Three ways to save an estimate; each must go through the rate step
    and send the decisions, or a fix would silently leave the library stale."""
    editor = (SRC / "app/(authed)/projects/[id]/workbench/estimates/[vid]/edit/EstimateEditor.tsx").read_text()
    assert editor.count("withRateStep(doc,") == 2  # save as new + update in place
    assert editor.count("rate_updates,") == 2
    prompt = (SRC / "components/EditWithPrompt.tsx").read_text()
    assert "previewRateWriteback" in prompt and "applyEditPlan(jobId, Array.from(accepted), asNewVersion, rateUpdates)" in prompt
    step = (SRC / "components/RateUpdatesStep.tsx").read_text()
    assert "disabled={busy || missing.length > 0}" in step  # a job-only rate needs its reason


def test_an_estimate_line_shows_the_rate_that_priced_it():
    table = (SRC / "app/(authed)/projects/[id]/estimate/[vid]/DivisionTable.tsx").read_text()
    assert "<LinkNote link={it.rate_link} />" in table
    # Never on the client's view: rate codes and AI figures are internal.
    assert "clientView || !it || it.contingency || !it.rate_link" in table


def test_the_rate_library_page_has_the_approved_layout():
    """Phase 4 wireframe C (Amir, 2026-10-16): divisions + filters + review tray,
    a catalogue tab, an imports tab with undo, and every row's age."""
    page = (SRC / "app/(authed)/rates/page.tsx").read_text()
    for tab in ('"assemblies"', '"materials"', '"labour"', '"catalogue"', '"imports"'):
        assert f"key: {tab}" in page
    assert page.count("<LibraryLayout") == 3  # assemblies, materials, labour
    assert page.count("<AgeCell") == 3
    assert "Review tray" in page and "<ImportWizard" in page
    imports = (SRC / "app/(authed)/rates/ImportsTab.tsx").read_text()
    assert "latestApplied?.id === r.id" in imports  # only the latest import offers undo


def test_keystone_never_sends_a_milestone_email():
    """Amir, 2026-10-16: "There should always be a confirmation before the emails
    go out"; then 2026-10-17: "I will not be using the AWS email sending. I will
    send the emails myself." The panel has no send call at all: drafts leave only
    by Copy to Gmail. The API keeps its confirm-gated send for later."""
    panel = (SRC / "app/(authed)/projects/[id]/milestones/EmailPanel.tsx").read_text()
    assert "sendMilestoneEmail" not in panel and "Review and send" not in panel
    assert "Copy to Gmail" in panel
    lib = (SRC / "lib/milestones.ts").read_text()
    assert lib.count("confirm: true") == 1


def test_copy_to_gmail_always_reminds_to_bcc_the_project_address():
    """Amir, 2026-10-17: "make a reminder every time you're copying to send the
    email from the Gmail to BCC the project email as well." The Gmail link carries
    the BCC, and the panel says so in words next to it, with the address."""
    panel = (SRC / "app/(authed)/projects/[id]/milestones/EmailPanel.tsx").read_text()
    href = panel[panel.index("const gmailHref"):panel.index("const placeholder")]
    assert "bcc=${encodeURIComponent(email.project_address)}" in href
    note = panel[panel.index("Sending from Gmail:"):panel.rindex("Copy to Gmail")]
    assert "BCC" in note and "{email.project_address}" in note
    assert "setGmailOpened(true)" in panel and "clipboard" in panel


def test_submittal_questions_leave_only_by_gmail_with_the_bcc_reminder():
    """Wireframe D (approved 2026-10-17): question emails are copied to Gmail, never
    sent by Keystone, and every one carries the BCC reminder and the address."""
    folder = SRC / "app/(authed)/projects/[id]/submittals"
    src = "".join(p.read_text() for p in folder.rglob("*.tsx"))
    assert "sendMilestoneEmail" not in src and "/send" not in src
    email = (folder / "[sid]/QuestionEmail.tsx").read_text()
    note = email[email.index("Sending from Gmail:"):email.rindex("Copy to Gmail")]
    assert "BCC" in note and "{e.project_address}" in note
    lib = (SRC / "lib/submittals.ts").read_text()
    href = lib[lib.index("export function gmailHref"):]
    assert "&bcc=${encodeURIComponent" in href and "e.project_address" in href


def test_submittals_sit_under_documents():
    """Amir, 2026-10-17: Documents › Submittals, not a tab of its own."""
    tabs = (SRC / "app/(authed)/projects/[id]/ProjectTabs.tsx").read_text()
    docs = tabs[tabs.index('label: "Documents"'):tabs.index('label: "Scope"')]
    assert '"/submittals"' in docs


def test_the_three_follow_ups_are_wired_into_the_pages():
    """Amir, 2026-10-17: "Finish the ones that are not yet built": the citation
    shows the page with the cited words boxed, a quote line can go to the rate
    library, and a checklist can be edited from the Start page."""
    folder = SRC / "app/(authed)/projects/[id]/submittals"
    review = (folder / "[sid]/page.tsx").read_text()
    assert "getCitation(" in review and "Boxed:" in review
    assert "quoteLineToLibrary(" in review and "Add to rate library" in review
    start = (folder / "new/page.tsx").read_text()
    assert "<ChecklistEditor" in start and "Edit this checklist" in start
    assert "saveChecklist(" in (folder / "new/ChecklistEditor.tsx").read_text()


def test_the_gmail_subject_is_tagged_from_the_text_being_sent():
    """Audit 2026-10-17: Gmail got the subject from when the draft was opened, so
    an edited subject no longer matched the saved one and our BCC copy was filed
    as a reply. The tag is now added to the current text, by the server's rule,
    and Copy is refused while the draft is older than the questions."""
    import re as _re

    from app.services.milestones import tag_subject

    lib = (SRC / "lib/submittals.ts").read_text()
    href = lib[lib.index("export function gmailHref"):]
    assert "taggedSubject(e.subject, ref)" in href and "subject_as_sent" not in href
    js = _re.search(r'subject\.replace\(/(.+?)/gi, ""\)\.trim\(\)', lib).group(1).replace("\\\\", "\\")
    for subj in ("1234 tank questions", "1234 tank [KS-S1] questions", "x [ks-m4]"):
        ours = f'{_re.sub(js, "", subj, flags=_re.I).strip()} [KS-S1]'
        assert ours == tag_subject(subj, "S1"), subj
    email = (SRC / "app/(authed)/projects/[id]/submittals/[sid]/QuestionEmail.tsx").read_text()
    assert "if (stale)" in email and "ev.preventDefault()" in email


def test_the_pdf_measuring_page_does_not_call_its_geometry_ai():
    """Rollout 2026-10-20: the help text said "The AI scans the PDF" and
    "Sonnet uses your number"; the stroke grouping is plain geometry, and the
    page was going to be shown to people who would take that literally."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "quantities" / "[discipline]" / "page.tsx").read_text()
    assert "The AI scans" not in src and "Sonnet" not in src


def test_the_pdf_measuring_page_opens_on_its_own_discipline():
    """Rollout 2026-10-20: the Architectural page opened on a stormwater set
    with no scale on any page, which showed "0 measurable" and nothing to do."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "quantities" / "[discipline]" / "page.tsx").read_text()
    assert "drawingGroup(d.filename) === discipline" in src


def test_a_sign_off_with_unchecked_measurements_does_not_say_the_estimate_can_use_them():
    """From-scratch test 2026-10-20: a PDF length saved after sign-off left
    step 3 reading "The estimate can use these numbers" while the gate held
    the estimate on that unchecked row."""
    src = (SRC / "app" / "(authed)" / "projects" / "[id]" / "takeoff" / "page.tsx").read_text()
    at = src.index("The estimate can use these numbers")
    assert src.rfind("signedOff && openCount > 0", 0, at) != -1


WB = SRC / "app" / "(authed)" / "projects" / "[id]" / "workbench"


def test_the_cash_flow_page_gives_no_made_up_credit_line_advice():
    """Round 2 build 2026-11-02: the cash flow page told every project to
    "Recommend LOC $200K" ($250K with options), a hard-coded number with no
    link to the project's own lowest cash point."""
    assert "Recommend LOC" not in (WB / "cashflow" / "page.tsx").read_text()


def test_cash_flow_and_sov_are_one_money_sub_tab():
    """Amir 2026-10-23: cash flow and SOV stay separate pages but sit under ONE
    Money sub-tab, switched on the page."""
    tabs = (SRC / "app" / "(authed)" / "projects" / "[id]" / "ProjectTabs.tsx").read_text()
    assert 'label: "Cash flow & SOV"' in tabs
    assert 'label: "Schedule of values"' not in tabs and 'label: "Cash flow" }' not in tabs
    for page in ("cashflow", "sov"):
        assert "<CashSovSwitch" in (WB / page / "page.tsx").read_text(), page


def test_a_plan_built_sov_does_not_show_a_10pct_holdback_every_month():
    """1234's holdback is its own contract stage (the last 10%), so a 10%
    per-month holdback row under a billing-plan SOV double-counts it."""
    sov = (WB / "sov" / "page.tsx").read_text()
    i = sov.index("Lien holdback 10%")
    assert "{!fromPlan && <>" in sov[i - 300:i]


PROJ = SRC / "app" / "(authed)" / "projects" / "[id]"


def test_messages_and_conversations_are_one_conversation_page():
    """Round 2 (Amir 2026-10-23): one Client › Conversation. The old addresses
    redirect so bookmarks and emailed links still land."""
    for old in ("messages", "conversations"):
        assert "redirect(`/projects/${id}/conversation`)" in (PROJ / old / "page.tsx").read_text(), old
    tabs = (PROJ / "ProjectTabs.tsx").read_text()
    assert '{ path: "/conversation", label: "Conversation" }' in tabs
    assert 'label: "Messages"' not in tabs and 'label: "Conversations"' not in tabs


def test_a_tell_the_client_draft_still_reaches_the_reply_box():
    """S3/S4 put a "Tell the client" draft in browser storage for the Messages
    composer. Messages became Conversation; without the pickup there, the
    draft would sit in storage and the links would open an empty box."""
    conv = (PROJ / "conversation" / "page.tsx").read_text()
    assert "takeMessageDraft(id)" in conv and "clearMessageDraft(id)" in conv
    for f in ("RecordForm.tsx", "UpdatePlan.tsx"):
        src = (PROJ / "schedule" / "record" / f).read_text()
        assert "/conversation`" in src and "/messages`" not in src, f


def test_proposal_lives_under_client_and_in_the_portal():
    """Round 2 (R2Proposal): Presentation moves out of More to Client ›
    Proposal, and the client has a Proposal page to accept it."""
    tabs = (PROJ / "ProjectTabs.tsx").read_text()
    assert '{ path: "/workbench/presentation", label: "Proposal" }' in tabs
    assert 'label: "Presentation"' not in tabs
    assert '"/portal/proposal"' in (SRC / "app" / "portal" / "layout.tsx").read_text()


def test_the_backend_proxy_passes_on_who_and_from_where():
    """A proposal acceptance records the client's IP and browser; the proxy
    dropped every header but cookies and content type, so the backend saw only
    Vercel's address."""
    proxy = (SRC / "app" / "api" / "backend" / "[...path]" / "route.ts").read_text()
    assert 'headers["X-Forwarded-For"]' in proxy and 'headers["User-Agent"]' in proxy


def test_libraries_and_settings_are_two_sidebar_groups():
    """Round 2 (R2Libraries, R2Settings): eight flat items become two groups,
    and in the collapsed icon rail (the default) a group still reaches every
    page through its menu."""
    layout = (SRC / "app" / "(authed)" / "layout.tsx").read_text()
    assert 'label: "Libraries"' in layout and 'label: "Settings"' in layout
    for old in ('label: "Rate Library"', 'label: "Trade Library"', 'label: "Cost Divisions"'):
        assert old not in layout, old
    side = (SRC / "components" / "AppSidebar.tsx").read_text()
    assert "item.children && collapsed" in side and "<details" in side


def test_no_fetch_failure_renders_as_an_empty_list():
    """#59 (2026-10-05): `.catch(() => [])` turned an API outage into "No
    contract yet" / "No estimate yet" — the contract page then offered
    Generate, inviting a duplicate. Failures go through lib/loadOr and show
    <LoadFailed/>; this keeps the pattern from coming back."""
    bad = []
    for path in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        if ".next" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("//")[0]
            if code.lstrip().startswith(("*", "/*")):  # doc comments that name the old pattern
                continue
            if re.search(r"\.catch\(\(\) => \[\]", code):
                bad.append(f"{path.relative_to(SRC)}:{n}")
    assert not bad, bad


def test_no_screen_or_saved_note_sends_anyone_to_brio_admin():
    """brio-admin is discontinued (merged into Keystone). The cash-flow note
    written by regenerate-downstream told the reader to 'rerun the brio-admin
    cash-flow builder', a tool that no longer exists."""
    wb = (Path(__file__).resolve().parents[1] / "app" / "routers" / "workbench.py").read_text()
    for line in wb.splitlines():
        if "brio-admin" in line and not line.lstrip().startswith("#"):
            raise AssertionError(line.strip())


def test_money_opens_the_tab_a_link_asks_for():
    """Verification 2026-11-02: Ask's budget answer links to
    /money?view=costs, but the Money page always opened on Summary."""
    money = (PROJ / "money" / "page.tsx").read_text()
    assert 'useSearchParams().get("view")' in money
    asst = (Path(__file__).resolve().parents[1] / "app" / "services" / "assistant.py").read_text()
    assert "/money?view=costs" in asst


def test_selections_page_follows_the_board():
    """R2Selections: drafts until sent, decide-by from the schedule, confirm
    drafts a change order; the portal shows decide-by as a calendar day."""
    page = (PROJ / "selections" / "page.tsx").read_text()
    for s in ("getSelectionPlan", "Suggest from the schedule", "Send to client", "confirmSelection", "Decide by"):
        assert s in page, s
    portal_sel = (SRC / "app" / "portal" / "selections" / "page.tsx").read_text()
    assert "decide_by" in portal_sel and 'T12:00:00Z' in portal_sel


def test_sov_and_cash_flow_follow_the_billing_board():
    """R2Billing: SOV shows % done / earned / billed / left with a PDF; the
    cash flow shows money in and out by month."""
    sov = (WB / "sov" / "page.tsx").read_text()
    for s in ("getSovProgress", "% done", "Earned", "Billed", "Left", "sov.pdf"):
        assert s in sov, s
    assert "Money in and out, by month" in (WB / "cashflow" / "page.tsx").read_text()


def test_contract_page_has_the_boards_checks_and_late_banner():
    page = (WB / "contract" / "page.tsx").read_text()
    assert "<LateBanner" in page and "<ContractChecks" in page
    comp = (WB / "contract" / "ContractChecks.tsx").read_text()
    for s in ("Draft a time-extension change order", "Upload the countersigned PDF", "Amend (new version)"):
        assert s in comp, s


def test_conversation_has_decisions_bookings_and_schedule_reply():
    conv = (PROJ / "conversation" / "page.tsx").read_text()
    for s in ("Decisions on record", "Suggest a reply from the schedule", "marked booked?"):
        assert s in conv, s


def test_proposal_follows_the_board():
    panel = (WB / "presentation" / "PortalPanel.tsx").read_text()
    for s in ("Opened", "Accepted → contract", "Preview as client", "Edit words", "Rebuild it"):
        assert s in panel, s
    portal_p = (SRC / "app" / "portal" / "proposal" / "page.tsx").read_text()
    assert "Options" in portal_p and "dropped" in portal_p


def test_libraries_has_its_landing_page():
    """R2Libraries: one menu item opens a page with Needs you and a card per library."""
    page = (SRC / "app" / "(authed)" / "libraries" / "page.tsx").read_text()
    for s in ("Needs you", "Supplier catalogue", "Cost trends", "Actual costs", "/libraries/overview"):
        assert s in page, s
    assert 'href: "/libraries", label: "Libraries"' in (SRC / "app" / "(authed)" / "layout.tsx").read_text()


def test_settings_page_and_portal_warranty_follow_round_2():
    """R2Settings: one Settings page; R2Main: Warranty moves into the client's menu."""
    page = (SRC / "app" / "(authed)" / "settings" / "page.tsx").read_text()
    for s in ("Team & access", "What each role can do", "How we work", "Email", "/settings/overview"):
        assert s in page, s
    assert 'href: "/settings", label: "Settings"' in (SRC / "app" / "(authed)" / "layout.tsx").read_text()
    assert '"/portal/warranty"' in (SRC / "app" / "portal" / "layout.tsx").read_text()
