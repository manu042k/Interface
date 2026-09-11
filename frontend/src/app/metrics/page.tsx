"use client";

import { useQuery } from "@tanstack/react-query";
import {
  TrendingDown,
  TrendingUp,
  Minus,
  Activity,
  Coins,
  DollarSign,
  PiggyBank,
  Gauge,
  ShieldCheck,
  Percent,
  BarChart3,
  type LucideIcon,
} from "lucide-react";
import { api, type Metrics, type MetricSeriesPoint } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";

const fmt = new Intl.NumberFormat("en-US");
const usd = (n: number) =>
  n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;

export default function MetricsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["metrics"],
    queryFn: api.metrics,
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Metrics"
        description="Token spend, cost, and reliability across every discovery and replay run — and whether the record-once / replay-many trade is paying off."
      />

      {isLoading || !data ? (
        <p className="text-muted-foreground text-sm">Loading metrics…</p>
      ) : (
        <Body m={data} />
      )}
    </div>
  );
}

function Body({ m }: { m: Metrics }) {
  const o = m.overview;
  const e = m.efficiency;
  return (
    <>
      <TrendBanner trend={m.trend} />

      <SectionHeading>Overview</SectionHeading>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat icon={Activity} label="Total runs" value={fmt.format(o.total_runs)}
          sub={`${o.discovery_runs} discovery · ${o.replay_invocations} replay`} />
        <Stat icon={Coins} label="Tokens (in / out)" value={fmt.format(o.tokens_total)}
          sub={`${fmt.format(o.tokens_in)} in · ${fmt.format(o.tokens_out)} out · ${o.llm_calls} calls`} />
        <Stat icon={DollarSign} label="Est. LLM cost" value={usd(o.est_cost_usd)}
          sub={`@ $${o.cost_rate_per_mtok.in}/$${o.cost_rate_per_mtok.out} per Mtok`} />
        <Stat icon={PiggyBank} label="Saved by replay" value={usd(e.est_cost_saved_usd)} accent="success"
          sub={`${fmt.format(e.tokens_saved_by_replay)} tokens not re-reasoned`} />
      </div>

      <SectionHeading>Efficiency &amp; reliability</SectionHeading>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle><TitleIcon icon={Gauge} />Efficiency per discovery run</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row k="Avg tokens / discovery" v={fmt.format(e.avg_tokens_per_discovery)} />
            <Row k="Avg LLM calls / discovery" v={String(e.avg_llm_calls_per_discovery)} />
            <Row k="Avg steps / discovery" v={String(e.avg_steps_per_discovery)} />
            <Row k="Replay share of all invocations" v={`${e.replay_share_pct}%`} />
            <Row k="Cost per successful discovery" v={usd(e.cost_per_successful_run_usd)} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle><TitleIcon icon={ShieldCheck} />Reliability</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div>
              <p className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">Discovery</p>
              <Row k="Completed" v={`${m.reliability.discovery.completed} (${m.reliability.discovery.success_rate_pct}%)`} />
              <Row k="Needed a human" v={`${m.reliability.discovery.needs_human} (${m.reliability.discovery.escalation_rate_pct}%)`} />
              <Row k="Business outcome" v={String(m.reliability.discovery.business_outcome)} />
              <Row k="Dead-end / failed" v={String(m.reliability.discovery.dead_end_or_failed)} />
            </div>
            <div>
              <p className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">Replay</p>
              <Row k="Succeeded" v={`${m.reliability.replay.ok} (${m.reliability.replay.success_rate_pct}%)`} />
              <Row k="Hard failures" v={String(m.reliability.replay.failed)} />
            </div>
            <div>
              <p className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">Duration (p50 / p95, s)</p>
              <Row k="Discovery" v={`${m.reliability.duration_seconds.discovery_p50} / ${m.reliability.duration_seconds.discovery_p95}`} />
              <Row k="Replay" v={`${m.reliability.duration_seconds.replay_p50} / ${m.reliability.duration_seconds.replay_p95}`} />
            </div>
          </CardContent>
        </Card>
      </div>

      <SectionHeading>Trends by day</SectionHeading>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle><TitleIcon icon={Coins} />Avg tokens per discovery, by day</CardTitle></CardHeader>
          <CardContent>
            <LineChart
              series={m.series}
              pick={(p) => p.avg_tokens_per_discovery}
              lowerIsBetter
              fmtY={(n) => fmt.format(Math.round(n))}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle><TitleIcon icon={Percent} />Replay share of invocations, by day</CardTitle></CardHeader>
          <CardContent>
            <LineChart
              series={m.series}
              pick={(p) => p.replay_share_to_date}
              fmtY={(n) => `${Math.round(n)}%`}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle><TitleIcon icon={BarChart3} />Runs per day</CardTitle></CardHeader>
        <CardContent>
          <StackedBars series={m.series} />
        </CardContent>
      </Card>
    </>
  );
}

/* ---------- small pieces ---------- */

function TitleIcon({ icon: Icon }: { icon: LucideIcon }) {
  return <Icon className="text-muted-foreground mr-2 inline-block h-4 w-4 -translate-y-px" />;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <h2 className="text-muted-foreground shrink-0 text-xs font-medium uppercase tracking-wide">
        {children}
      </h2>
      <Separator className="flex-1" />
    </div>
  );
}

function TrendBanner({ trend }: { trend: Metrics["trend"] }) {
  const map = {
    improving: { Icon: TrendingDown, cls: "border-success/40 bg-success/8 text-success", word: "Improving" },
    regressing: { Icon: TrendingUp, cls: "border-destructive/40 bg-destructive/8 text-destructive", word: "Regressing" },
    flat: { Icon: Minus, cls: "border-border bg-muted/40 text-muted-foreground", word: "Flat" },
  }[trend.direction];
  const { Icon } = map;
  return (
    <div className={cn("flex items-start gap-3 rounded-lg border p-4", map.cls)}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <p className="font-medium">Trend: {map.word}</p>
        <p className="mt-0.5 text-sm opacity-90">{trend.detail}</p>
      </div>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon?: LucideIcon;
  label: string;
  value: string;
  sub?: string;
  accent?: "success";
}) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
          {Icon && <Icon className="h-3.5 w-3.5" />}
          <span>{label}</span>
        </div>
        <p className={cn("font-heading mt-1 text-2xl font-medium tabular-nums", accent === "success" && "text-success")}>
          {value}
        </p>
        {sub && <p className="text-muted-foreground mt-1 text-xs">{sub}</p>}
      </CardContent>
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5">
      <span className="text-muted-foreground">{k}</span>
      <span className="tabular-nums">{v}</span>
    </div>
  );
}

function LineChart({
  series,
  pick,
  fmtY,
  lowerIsBetter,
}: {
  series: MetricSeriesPoint[];
  pick: (p: MetricSeriesPoint) => number;
  fmtY: (n: number) => string;
  lowerIsBetter?: boolean;
}) {
  const pts = series.map((p) => ({ date: p.date, y: pick(p) })).filter((p) => p.y > 0);
  if (pts.length < 2) {
    return <p className="text-muted-foreground text-sm">Not enough data yet.</p>;
  }
  const W = 520;
  const H = 140;
  const pad = { l: 44, r: 8, t: 8, b: 18 };
  const ys = pts.map((p) => p.y);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const x = (i: number) => pad.l + (i / (pts.length - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (H - pad.t - pad.b);
  const d = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.y)}`).join(" ");
  const first = pts[0].y;
  const last = pts[pts.length - 1].y;
  const good = lowerIsBetter ? last <= first : last >= first;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img">
      <line x1={pad.l} y1={y(min)} x2={W - pad.r} y2={y(min)} className="stroke-border" strokeWidth={1} />
      <line x1={pad.l} y1={y(max)} x2={W - pad.r} y2={y(max)} className="stroke-border" strokeWidth={1} strokeDasharray="3 3" />
      <text x={4} y={y(max) + 4} className="fill-muted-foreground text-[10px]">{fmtY(max)}</text>
      <text x={4} y={y(min) + 4} className="fill-muted-foreground text-[10px]">{fmtY(min)}</text>
      <path d={d} fill="none" strokeWidth={2}
        className={good ? "stroke-success" : "stroke-destructive"} />
      {pts.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.y)} r={2.5}
          className={good ? "fill-success" : "fill-destructive"}>
          <title>{`${p.date}: ${fmtY(p.y)}`}</title>
        </circle>
      ))}
      <text x={pad.l} y={H - 4} className="fill-muted-foreground text-[10px]">{pts[0].date}</text>
      <text x={W - pad.r} y={H - 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
        {pts[pts.length - 1].date}
      </text>
    </svg>
  );
}

function StackedBars({ series }: { series: MetricSeriesPoint[] }) {
  if (series.length === 0) {
    return <p className="text-muted-foreground text-sm">No runs yet.</p>;
  }
  const max = Math.max(...series.map((p) => p.discovery_runs + p.replay_runs), 1);
  return (
    <div className="space-y-1.5">
      {series.map((p) => (
        <div key={p.date} className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground w-20 shrink-0 tabular-nums">{p.date}</span>
          <div className="flex h-4 flex-1 overflow-hidden rounded-sm bg-muted/50">
            <div className="bg-primary/70 h-full" style={{ width: `${(p.discovery_runs / max) * 100}%` }} />
            <div className="bg-success/70 h-full" style={{ width: `${(p.replay_runs / max) * 100}%` }} />
          </div>
          <span className="w-16 shrink-0 text-right tabular-nums">
            {p.discovery_runs}d · {p.replay_runs}r
          </span>
        </div>
      ))}
      <p className="text-muted-foreground pt-1 text-[11px]">
        <span className="text-primary">■</span> discovery ·{" "}
        <span className="text-success">■</span> replay
      </p>
    </div>
  );
}
