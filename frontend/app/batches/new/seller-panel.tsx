"use client";

import { useEffect, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { SellerProfile } from "@/lib/types";

// What "we" sell, read-only. Every hook is scored and every draft written against this.
export function SellerPanel() {
  const [seller, setSeller] = useState<SellerProfile | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api.getSeller().then(setSeller, () => setFailed(true));
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Selling as {seller?.company ?? "…"}</CardTitle>
        <CardDescription>Hooks are scored and drafts written against this profile.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {failed && <p className="text-muted-foreground">Couldn&apos;t load the seller profile.</p>}
        {seller && (
          <>
            <Section title="What we sell">{seller.offer}</Section>
            <Section title="Problem we solve">{seller.problem_solved}</Section>
            <Section title="Buyers">{seller.icp.buyer_titles.join(", ")}</Section>
            <Section title="Tone">
              {seller.tone.voice}; under {seller.tone.max_words} words
            </Section>
            <p className="text-xs text-muted-foreground">
              Sent as {seller.rep.name}, {seller.rep.title}. Edit seller_profile.yaml to change.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</p>
      <p className="mt-1 leading-relaxed">{children}</p>
    </div>
  );
}
