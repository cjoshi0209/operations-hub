import { useEffect } from "react";

export function useDocumentTitle(title: string | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · Operations Hub` : "Operations Hub";
    return () => {
      document.title = "Operations Hub";
    };
  }, [title]);
}
