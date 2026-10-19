import { ReviewQueue } from "./review-queue";

export default async function ReviewPage(props: PageProps<"/batches/[id]/review">) {
  const { id } = await props.params;
  return <ReviewQueue batchId={Number(id)} />;
}
