import { BatchView } from "./batch-view";

export default async function BatchPage(props: PageProps<"/batches/[id]">) {
  const { id } = await props.params;
  return <BatchView batchId={Number(id)} />;
}
