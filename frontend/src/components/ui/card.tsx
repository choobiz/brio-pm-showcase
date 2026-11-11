import { HTMLAttributes } from "react";

export function Card({ className = "", ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`bg-white rounded-md shadow-sm border border-slate-200 ${className}`} {...rest} />
  );
}
