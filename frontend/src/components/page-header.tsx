import { cn } from "@/lib/utils";
import { sentenceCase } from "@/lib/text";

export function PageHeader({
  title,
  description,
  children,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <header
      className={cn(
        "flex flex-wrap items-start justify-between gap-3",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="font-heading truncate text-3xl font-medium tracking-tight">
          {typeof title === "string" ? sentenceCase(title) : title}
        </h1>
        {description ? (
          <p className="text-muted-foreground mt-1 line-clamp-2 text-sm">
            {description}
          </p>
        ) : null}
      </div>
      {children}
    </header>
  );
}
