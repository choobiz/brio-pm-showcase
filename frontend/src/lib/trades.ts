// Trade Library (cross-project trades / subcontractors / suppliers) — office side,
// via the /api/backend proxy.

const BASE = "/api/backend";

export type TradeCategory =
  | "excavation" | "concrete" | "framing" | "roofing" | "windows_doors"
  | "insulation" | "drywall" | "plumbing" | "electrical" | "hvac"
  | "flooring" | "tile" | "painting" | "cabinets_millwork" | "masonry"
  | "waterproofing" | "landscaping" | "fire_protection" | "low_voltage"
  | "demolition" | "cleaning" | "disposal" | "engineering" | "architecture"
  | "survey" | "supplier" | "other"
  // Added with the shared trade list (backend services/library_vocab.TRADES,
  // 2026-10-15); a backend test keeps this list equal to that one.
  | "general_conditions" | "general_labour" | "finish_carpentry" | "appliances"
  | "siding" | "stormwater";

export const TRADE_CATEGORY_LABELS: Record<TradeCategory, string> = {
  excavation: "Excavation", concrete: "Concrete", framing: "Framing",
  roofing: "Roofing", windows_doors: "Windows & Doors", insulation: "Insulation",
  drywall: "Drywall", plumbing: "Plumbing", electrical: "Electrical", hvac: "HVAC",
  flooring: "Flooring", tile: "Tile", painting: "Painting",
  cabinets_millwork: "Cabinets & Millwork", masonry: "Masonry",
  waterproofing: "Waterproofing", landscaping: "Landscaping",
  fire_protection: "Fire Protection", low_voltage: "Low Voltage",
  demolition: "Demolition", cleaning: "Cleaning", disposal: "Disposal",
  engineering: "Engineering", architecture: "Architecture", survey: "Survey",
  supplier: "Supplier", other: "Other",
  general_conditions: "General Conditions & PM", general_labour: "General Labour",
  finish_carpentry: "Finish Carpentry", appliances: "Appliances",
  siding: "Siding & Cladding", stormwater: "Stormwater & Drainage",
};

export const TRADE_CATEGORIES = Object.keys(TRADE_CATEGORY_LABELS) as TradeCategory[];


/** Residential division each trade prices under (mirrors backend library_vocab.TRADES; a test keeps them equal). */
export const TRADE_DIVISION: Record<TradeCategory, string | null> = {
  general_conditions: "01",
  general_labour: "01",
  engineering: "01",
  architecture: "01",
  survey: "01",
  cleaning: "01",
  demolition: "02",
  disposal: "02",
  excavation: "02",
  concrete: "03",
  waterproofing: "03",
  masonry: "03",
  framing: "04",
  windows_doors: "05",
  insulation: "06",
  drywall: "06",
  painting: "07",
  flooring: "08",
  tile: "08",
  finish_carpentry: "09",
  cabinets_millwork: "09",
  fire_protection: "10",
  appliances: "11",
  hvac: "12",
  plumbing: "13",
  electrical: "14",
  low_voltage: "14",
  roofing: "15",
  siding: "16",
  landscaping: "17",
  stormwater: "18",
  supplier: null,
  other: null,
};

export type TradeStatus = "unconfirmed" | "active" | "archived";

export interface Trade {
  id: string;
  company_name: string | null;
  contact_name: string | null;
  trade_category: TradeCategory;
  email: string | null;
  phone: string | null;
  website: string | null;
  tags: string[];
  notes: string | null;
  rating: number | null;
  status: TradeStatus;
  source: "manual" | "communication";
  source_communication_id: string | null;
  first_project_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TradeSummary {
  status_counts: Record<string, number>;
  category_counts: Record<string, number>;
}

export interface TradeInput {
  company_name?: string | null;
  contact_name?: string | null;
  trade_category?: string;
  email?: string | null;
  phone?: string | null;
  website?: string | null;
  notes?: string | null;
  tags?: string[];
  rating?: number | null;
  status?: TradeStatus;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.status === 204 ? (undefined as T) : (r.json() as Promise<T>);
}

export const listTrades = (opts?: { q?: string; category?: string; status?: string }) => {
  const p = new URLSearchParams();
  if (opts?.q) p.set("q", opts.q);
  if (opts?.category) p.set("category", opts.category);
  if (opts?.status) p.set("status_filter", opts.status);
  const qs = p.toString();
  return api<Trade[]>(`/trades${qs ? `?${qs}` : ""}`);
};

export const getTradeSummary = () => api<TradeSummary>(`/trades/summary`);

export const createTrade = (body: TradeInput) =>
  api<Trade>(`/trades`, { method: "POST", body: JSON.stringify(body) });

export const updateTrade = (id: string, body: TradeInput) =>
  api<Trade>(`/trades/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const confirmTrade = (id: string) =>
  api<Trade>(`/trades/${id}/confirm`, { method: "POST" });

export const deleteTrade = (id: string) =>
  api<void>(`/trades/${id}`, { method: "DELETE" });
