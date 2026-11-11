"""BC-specific document checklist by project phase.

Each item: { id, label, required, bc_specific }
- required: blocks phase progression if missing
- bc_specific: BC/WorkSafeBC/municipal regulatory item (vs generic GC doc)
"""

from typing import TypedDict


class ChecklistItem(TypedDict):
    id: str
    label: str
    required: bool
    bc_specific: bool


BC_DOC_CHECKLIST: dict[str, list[ChecklistItem]] = {
    "lead": [
        {"id": "site_visit_notes", "label": "Site visit notes", "required": True, "bc_specific": False},
        {"id": "site_photos_video", "label": "Site photos / video", "required": True, "bc_specific": False},
        {"id": "preliminary_scope", "label": "Preliminary scope", "required": True, "bc_specific": False},
        {"id": "quotation_pdf", "label": "Quotation PDF", "required": True, "bc_specific": False},
    ],
    "preconstruction": [
        {"id": "signed_contract", "label": "Signed contract", "required": True, "bc_specific": False},
        {"id": "deposit_invoice", "label": "Deposit invoice", "required": True, "bc_specific": False},
        {"id": "building_permit", "label": "Building permit", "required": True, "bc_specific": True},
        {"id": "design_drawings", "label": "Design drawings", "required": True, "bc_specific": False},
        {"id": "engineering_reports", "label": "Engineering reports", "required": False, "bc_specific": False},
        {"id": "owner_signoff", "label": "Owner sign-off", "required": True, "bc_specific": False},
        {"id": "gc_liability_insurance", "label": "GC liability insurance certificate", "required": True, "bc_specific": False},
        {"id": "worksafebc_clearance_brio", "label": "WorkSafeBC clearance — BRIO", "required": True, "bc_specific": True},
        {"id": "worksafebc_clearance_subs", "label": "WorkSafeBC clearance — each sub", "required": True, "bc_specific": True},
        {"id": "asbestos_test_pre1990", "label": "Asbestos test (required for pre-1990 homes)", "required": True, "bc_specific": True},
    ],
    "in_progress": [
        {"id": "change_orders", "label": "Change orders", "required": False, "bc_specific": False},
        {"id": "progress_invoices", "label": "Progress invoices", "required": True, "bc_specific": False},
        {"id": "daily_logs", "label": "Daily logs", "required": True, "bc_specific": False},
        {"id": "supplier_pos", "label": "Supplier POs", "required": False, "bc_specific": False},
        {"id": "inspection_reports", "label": "Inspection reports", "required": True, "bc_specific": False},
        {"id": "progress_photos_video", "label": "Progress photos / video", "required": True, "bc_specific": False},
        {"id": "rfis", "label": "RFIs", "required": False, "bc_specific": False},
        {"id": "worksafebc_clearance_ongoing", "label": "Ongoing WorkSafeBC clearance", "required": True, "bc_specific": True},
    ],
    "closeout": [
        {"id": "final_invoice", "label": "Final invoice", "required": True, "bc_specific": False},
        {"id": "occupancy_permit", "label": "Occupancy permit / final inspection sign-off", "required": True, "bc_specific": True},
        {"id": "warranty_docs", "label": "Warranty docs", "required": True, "bc_specific": False},
        {"id": "as_built_drawings", "label": "As-built drawings", "required": True, "bc_specific": False},
        {"id": "signed_punch_list", "label": "Signed punch list", "required": True, "bc_specific": False},
        {"id": "form7_lien_releases", "label": "Form 7 lien releases / holdback waivers", "required": True, "bc_specific": True},
        {"id": "worksafebc_clearance_final", "label": "Final WorkSafeBC clearance", "required": True, "bc_specific": True},
    ],
}


def get_checklist_for_phase(phase: str) -> list[ChecklistItem]:
    return BC_DOC_CHECKLIST.get(phase, [])
