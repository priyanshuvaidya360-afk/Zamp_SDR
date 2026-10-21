import { redirect } from "next/navigation";

// Uploading now lives on the home page (step 1 of the flow).
export default function NewBatchPage() {
  redirect("/");
}
