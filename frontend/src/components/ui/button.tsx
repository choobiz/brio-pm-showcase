import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const styles: Record<Variant, string> = {
  primary: "bg-[var(--color-brio-primary)] text-white hover:opacity-90",
  secondary: "bg-slate-100 text-slate-900 hover:bg-slate-200",
  ghost: "bg-transparent text-slate-700 hover:bg-slate-100",
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = "primary", className = "", ...rest }, ref) => (
    <button
      ref={ref}
      className={`px-4 py-2 rounded-md font-medium text-sm transition ${styles[variant]} ${className}`}
      {...rest}
    />
  ),
);

Button.displayName = "Button";
