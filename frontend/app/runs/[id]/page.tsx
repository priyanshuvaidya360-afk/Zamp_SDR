import { RunView } from "./run-view";

export default async function RunPage(props: PageProps<"/runs/[id]">) {
  const { id } = await props.params;
  return <RunView runId={Number(id)} />;
}
