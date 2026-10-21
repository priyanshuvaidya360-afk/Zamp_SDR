"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Prospects" },
  { href: "/dashboard", label: "Dashboard" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="bg-navy text-white">
      <nav className="mx-auto flex h-[60px] max-w-6xl items-center gap-10 px-6">
        <Link href="/" className="flex items-center gap-2.5 text-lg font-bold tracking-tight">
          <span className="flex size-[26px] items-center justify-center rounded-[7px] bg-[#3b82f6]">
            <span className="size-2.5 rounded-full bg-white" />
          </span>
          Signal
        </Link>
        <div className="flex gap-1.5 text-sm">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "rounded-lg px-3.5 py-2 text-[#c9d8f5] transition-colors hover:bg-white/10 hover:text-white",
                (link.href === "/" ? pathname === "/" || pathname.startsWith("/batches") || pathname.startsWith("/runs") : pathname.startsWith(link.href)) && "bg-white/10 font-semibold text-white",
              )}
            >
              {link.label}
            </Link>
          ))}
        </div>
      </nav>
    </header>
  );
}
