"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Play } from "lucide-react";
import { toast } from "sonner";
import { api, type Capability } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { OutcomeBadge, RiskBadge } from "@/components/badges";
import { sentenceCase } from "@/lib/text";

/**
 * Known-good values for ParaBank capabilities, verified live during manual
 * testing this session — not just each artifact's recorded `example`, which
 * can go stale (an account id, a password chosen at record time). Falls back
 * to the artifact's own example when a param isn't listed here, so every
 * field always starts filled, never blank.
 */
const KNOWN_GOOD: Record<string, Record<string, string>> = {
  parabank_login_failure: { password: "wrongpass123" },
  parabank_open_checking_account: {
    customer_address_street: "742 Evergreen Terrace",
    customer_password: "TestPass123!",
    repeatedpassword: "TestPass123!",
  },
  parabank_overdraw_transfer: { username: "john", password: "demo", amount: "999999" },
  parabank_bill_pay: {
    password: "demo",
    username: "john",
    payee_address_street: "123 Main St",
    payee_name: "Acme Utilities",
    payee_address_city: "Springfield",
    payee_address_state: "IL",
    payee_address_zipcode: "62704",
    payee_phonenumber: "555-100-2000",
    payee_accountnumber: "22446688",
    amount: "75",
  },
  parabank_request_loan_denied: { password: "demo" },
  parabank_request_loan_approved: { password: "demo" },
  parabank_transfer_between_two_accounts: {
    username: "john",
    password: "demo",
    fromaccountid: "13344",
    toaccountid: "13344",
  },
  parabank_transfer_gate_approve: {
    username: "john",
    password: "demo",
    amount: "50",
    fromaccountid: "13344",
    toaccountid: "13344",
  },
  parabank_transfer_gate_approve_v2: {
    username: "john",
    password: "demo",
    amount: "20",
    fromaccountid: "13344",
    toaccountid: "13344",
  },
  parabank_update_contact_info: {
    password: "demo",
    customer_address_street: "42 Wallaby Way",
  },
  parabank_balance_read: { password: "demo" },
  parabank_contact_us: {},
};

function prefillFor(cap: Capability): Record<string, string> {
  const known = KNOWN_GOOD[cap.name] ?? {};
  const out: Record<string, string> = {};
  for (const p of cap.inputs) {
    out[p.name] = known[p.name] ?? (p.example != null ? String(p.example) : "");
  }
  return out;
}

export default function PrefillRunsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });

  const caps = (data ?? []).filter((c) => c.name.startsWith("parabank_"));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Prefill runs"
        description="Every ParaBank capability, ready to run — fields are pre-filled with known-good values from live testing. Edit anything you like, or just hit Run."
      />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : caps.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No ParaBank capabilities found.
        </p>
      ) : (
        <div className="space-y-4">
          {caps.map((cap) => (
            <PrefillCard key={`${cap.artifact_id}-${cap.version}`} cap={cap} />
          ))}
        </div>
      )}
    </div>
  );
}

function PrefillCard({ cap }: { cap: Capability }) {
  const router = useRouter();
  const [params, setParams] = useState<Record<string, string>>(() => prefillFor(cap));

  const mut = useMutation({
    // wait_seconds: 0 - jump straight to the live run view, same UX as the
    // Capabilities page's own Invoke panel.
    mutationFn: () => api.invoke(cap.artifact_id, cap.version, null, params, "default", 0),
    onSuccess: (r) => {
      if (r.invocation_id) router.push(`/runs/${r.invocation_id}`);
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-heading text-base font-semibold tracking-tight">
                {sentenceCase(cap.name)}
              </h3>
              <RiskBadge risk={cap.risk_class} />
              <Badge variant="outline" className="text-muted-foreground text-[10px]">
                v{cap.version}
              </Badge>
            </div>
            <p className="text-muted-foreground line-clamp-2 text-sm">
              {cap.summary || cap.goal}
            </p>
          </div>
          <Button
            onClick={() => mut.mutate()}
            disabled={mut.isPending}
            className="shrink-0 gap-1.5"
          >
            <Play className="h-3.5 w-3.5" />
            {mut.isPending ? "Running…" : "Run"}
          </Button>
        </div>

        {cap.inputs.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {cap.inputs.map((p) => (
              <div key={p.name} className="space-y-1.5">
                <Label className="flex items-center gap-1.5">
                  {p.name}
                  {p.sensitive && (
                    <Badge
                      variant="outline"
                      className="text-warning border-warning/30 text-[10px]"
                    >
                      sensitive
                    </Badge>
                  )}
                </Label>
                <Input
                  type={p.sensitive ? "password" : "text"}
                  value={params[p.name] ?? ""}
                  onChange={(e) =>
                    setParams((s) => ({ ...s, [p.name]: e.target.value }))
                  }
                />
              </div>
            ))}
          </div>
        )}

        {mut.data && !mut.data.invocation_id && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
            <OutcomeBadge outcome={mut.data.outcome} />
            {mut.data.business_outcome_code && (
              <code className="text-warning text-xs">
                {mut.data.business_outcome_code}
              </code>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
