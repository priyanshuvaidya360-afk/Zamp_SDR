"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/batches/new", label: "New batch" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="border-b bg-white">
      <nav className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-6">
        <Link href="/" className="font-semibold tracking-tight">
          Signal
        </Link>
        <div className="flex gap-6 text-sm">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "text-muted-foreground transition-colors hover:text-foreground",
                pathname === link.href && "font-medium text-foreground",
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
