import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

# Without this, app loggers (`logging.getLogger(__name__)`) inherit a root logger
# that has no handler, so every INFO line is silently discarded — only uvicorn's
# own pre-configured loggers reach the container log. That silence is how the
# dropped inbound-email attachments went unnoticed. Uvicorn configures its own
# loggers, so this only affects ours.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s:     %(name)s: %(message)s",
)
from app.routers import (
    card_leads,
    milestones,
    submittals,
    assistant,
    prep,
    ai_edits,
    ai_jobs,
    analytics,
    change_orders,
    clients,
    communications,
    crm,
    daily_reports,
    schedule_actuals,
    schedule_replan,
    costs,
    billing_plan,
    conversation,
    proposal_portal,
    contract_checks,
    libraries,
    settings_overview,
    divisions,
    document_selection,
    documents,
    drawing_analysis,
    drawings,
    estimates,
    finish,
    home,
    money,
    inbound_email,
    invoices,
    manager,
    messages,
    payment_terms,
    portal,
    projects,
    public,
    quantity_overrides,
    rates,
    register,
    reviews,
    revisions,
    scopes,
    selections,
    skills,
    time_tracking,
    trades,
    users,
    workbench,
    proposals,
    drafts,
    line_rates,
    interview,
    qa_checklists,
)


app = FastAPI(title="brio-pm", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "brio-pm"}


app.include_router(projects.router)
app.include_router(clients.router)
app.include_router(card_leads.router)
app.include_router(crm.router)
app.include_router(card_leads.public)
app.include_router(documents.router)
app.include_router(document_selection.router)
app.include_router(revisions.router)
app.include_router(ai_edits.router)
app.include_router(proposals.router)
app.include_router(drafts.router)
app.include_router(line_rates.router)
app.include_router(interview.router)
app.include_router(qa_checklists.router)
app.include_router(drawings.router)
app.include_router(quantity_overrides.router)
app.include_router(drawing_analysis.router)
app.include_router(scopes.router)
app.include_router(estimates.router)
app.include_router(invoices.router)
app.include_router(ai_jobs.router)
app.include_router(analytics.router)
app.include_router(public.router)
app.include_router(workbench.router)
app.include_router(skills.router)
app.include_router(skills.runs_router)
app.include_router(rates.router)
app.include_router(daily_reports.router)
app.include_router(schedule_actuals.router)
app.include_router(schedule_replan.router)
app.include_router(costs.router)
app.include_router(billing_plan.router)
app.include_router(conversation.router)
app.include_router(proposal_portal.router)
app.include_router(contract_checks.router)
app.include_router(libraries.router)
app.include_router(settings_overview.router)
app.include_router(time_tracking.router)
app.include_router(reviews.router)
app.include_router(users.router)
app.include_router(portal.router)
app.include_router(manager.router)
app.include_router(change_orders.router)
app.include_router(selections.router)
app.include_router(messages.router)
app.include_router(communications.router)
app.include_router(trades.router)
app.include_router(inbound_email.router)
app.include_router(payment_terms.router)
app.include_router(register.router)
app.include_router(divisions.router)
app.include_router(home.router)
app.include_router(finish.router)
app.include_router(money.router)
app.include_router(assistant.router)
app.include_router(prep.router)
app.include_router(prep.worker)
app.include_router(milestones.router)
app.include_router(submittals.router)
app.include_router(submittals.checklist_router)
