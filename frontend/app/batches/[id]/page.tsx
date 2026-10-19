import { BatchStatus } from "./batch-status";

export default async function BatchPage(props: PageProps<"/batches/[id]">) {
  const { id } = await props.params;
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Batch #{id}</h1>
      <BatchStatus batchId={Number(id)} />
    </div>
  );
}
