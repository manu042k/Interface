"use client";

import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { api, type Capability } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { RiskBadge } from "@/components/badges";
import { sentenceCase } from "@/lib/text";

/**
 * Known-good param values for ParaBank capabilities, verified live during
 * testing this session — not just each artifact's recorded `example`, which
 * can go stale (an account id, a password chosen at record time). Falls back
 * to the artifact's own example when a param isn't listed here.
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

function prefillParams(cap: Capability): Record<string, string> {
  const known = KNOWN_GOOD[cap.name] ?? {};
  const out: Record<string, string> = {};
  for (const p of cap.inputs) {
    out[p.name] = known[p.name] ?? (p.example != null ? String(p.example) : "");
  }
  return out;
}

export default function PrefillRunsPage() {
  const router = useRouter();
  const { data, isLoading } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });

  const caps = (data ?? []).filter((c) => c.name.startsWith("parabank_"));

  function pick(cap: Capability) {
    const q = new URLSearchParams({
      goalName: sentenceCase(cap.name),
      description: cap.goal,
      target: cap.entry_url,
      params: JSON.stringify(prefillParams(cap)),
    });
    router.push(`/?${q.toString()}`);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Prefill runs"
        description="Pick a ParaBank capability — its goal, target, and known-good params fill the New run form. Review and hit Run there."
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : caps.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No ParaBank capabilities found.
        </p>
      ) : (
        <div className="space-y-2">
          {caps.map((cap) => (
            <Card
              key={`${cap.artifact_id}-${cap.version}`}
              className="hover:border-primary/40 cursor-pointer transition-colors"
              onClick={() => pick(cap)}
            >
              <CardContent className="flex items-center gap-3 p-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-heading text-sm font-semibold tracking-tight">
                      {sentenceCase(cap.name)}
                    </h3>
                    <RiskBadge risk={cap.risk_class} />
                    <Badge variant="outline" className="text-muted-foreground text-[10px]">
                      v{cap.version}
                    </Badge>
                  </div>
                  <p className="text-muted-foreground line-clamp-1 text-xs">
                    {cap.summary || cap.goal}
                  </p>
                </div>
                <ChevronRight className="text-muted-foreground h-4 w-4 shrink-0" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
