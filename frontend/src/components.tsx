import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { TriangleAlert } from 'lucide-react'

const buttonVariants = cva('button', {
  variants: { variant: { default: 'button-primary', outline: 'button-outline', ghost: 'button-ghost', destructive: 'button-danger' } },
  defaultVariants: { variant: 'default' },
})

export function Button({ className, variant, type = 'button', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button type={type} className={buttonVariants({ variant, className })} {...props} />
}

export function ErrorMessage({ error }: { error: string }) {
  return error && <div className="error" role="alert"><TriangleAlert size={17} />{error}</div>
}

export function EmptyState({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <div className="empty"><span className="empty-icon">{icon}</span><h3>{title}</h3>{children}</div>
}

export function formatDate(date: string) {
  const [year, month, day] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(year, month - 1, day))
}

export function today() {
  const date = new Date()
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}
