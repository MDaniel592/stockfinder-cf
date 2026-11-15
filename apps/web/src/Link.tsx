import type { AnchorHTMLAttributes, MouseEvent } from 'react';
import { go } from './router';

type Props = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & { href: string; replace?: boolean; state?: { from?: string } | null; onNavigate?: () => void };
/** Real anchor: modified clicks and middle clicks open in a new tab; plain clicks use the History API. */
export function Link({ href, replace, state, onNavigate, onClick, ...rest }: Props) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || rest.target === '_blank') return;
    event.preventDefault();
    onNavigate?.();
    go(href, { replace, state });
  };
  return <a href={href} onClick={handle} {...rest}/>;
}
