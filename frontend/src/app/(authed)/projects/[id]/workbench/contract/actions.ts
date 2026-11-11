"use server";

import { revalidatePath } from "next/cache";
import {
  createContract,
  setContractSignatories,
  setContractStatus,
  type ContractStatus,
  type Signatory,
} from "@/lib/workbench";

/** Save the contract's signatory list (rendered into the PDF signature block). */
export async function setSignatoriesAction(
  projectId: string,
  contractId: string,
  signatories: Signatory[],
) {
  const res = await setContractSignatories(projectId, contractId, signatories);
  revalidatePath(`/projects/${projectId}/workbench/contract`);
  return res;
}

/** Generate a contract record from the active estimate + schedule. */
export async function generateContractAction(projectId: string) {
  const res = await createContract(projectId);
  revalidatePath(`/projects/${projectId}/workbench/contract`);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return res;
}

/**
 * Advance (or revert) the contract lifecycle: draft → issued → signed →
 * executed. Signing captures the owner's name + date; executing records the
 * BRIO signatory. Revalidates the contract page so the status badge + Status
 * card refresh immediately.
 */
export async function setContractStatusAction(
  projectId: string,
  contractId: string,
  body: {
    status: ContractStatus;
    signed_by_owner?: string;
    signed_by_contractor?: string;
    signed_date?: string;
  },
) {
  const res = await setContractStatus(projectId, contractId, body);
  revalidatePath(`/projects/${projectId}/workbench/contract`);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return res;
}
