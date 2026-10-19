import {
  QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiError, request } from "./client";
import type {
  Comment,
  ItemDetail,
  ItemEvent,
  ItemPage,
  ItemType,
  ListFilters,
  Me,
  Notification,
  Page,
  Priority,
  Team,
  WorkItem,
  Status,
} from "./types";

export const PAGE_SIZE = 20;

// ---------- reads ----------
export const useMe = (enabled: boolean) =>
  useQuery({ queryKey: ["me"], queryFn: () => request<Me>("/auth/me"), enabled, staleTime: 5 * 60_000 });

export const useTeams = () =>
  useQuery({ queryKey: ["teams"], queryFn: () => request<Team[]>("/teams"), staleTime: 5 * 60_000 });

/** Cursor-paginated list. Only ever loads `PAGE_SIZE` rows at a time. */
export function useItems(filters: ListFilters, pageSize = PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: ["items", filters, pageSize],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      request<ItemPage>("/work-items", {
        query: { ...filters, limit: pageSize, cursor: pageParam },
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: (prev) => prev,
  });
}

export function useItem(id: number) {
  return useQuery({
    queryKey: ["item", id],
    queryFn: () => request<ItemDetail>(`/work-items/${id}`, { query: { events_limit: 20 } }),
    // Poll so "someone changed this under you" surfaces without a manual refresh.
    refetchInterval: 8000,
    refetchOnWindowFocus: true,
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
  });
}

export function useComments(id: number) {
  return useInfiniteQuery({
    queryKey: ["comments", id],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      request<Page<Comment>>(`/work-items/${id}/comments`, { query: { limit: 10, cursor: pageParam } }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

export const fetchOlderEvents = (id: number, cursor: string) =>
  request<Page<ItemEvent>>(`/work-items/${id}/events`, { query: { limit: 20, cursor } });

export const useNotifications = () =>
  useQuery({
    queryKey: ["notifications"],
    queryFn: () => request<Page<Notification>>("/notifications", { query: { limit: 10 } }),
    refetchInterval: 10_000,
  });

// ---------- reconciliation ----------
/** Put an authoritative server copy into the cache and refresh anything derived from it. */
export function reconcile(qc: QueryClient, item: WorkItem): void {
  qc.setQueryData<ItemDetail>(["item", item.id], (old) => (old ? { ...old, ...item } : old));
  void qc.invalidateQueries({ queryKey: ["item", item.id] }); // refresh the event timeline
  void qc.invalidateQueries({ queryKey: ["items"] });
}

// ---------- writes (each call = one user action = one Idempotency-Key) ----------
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body, idempotent: true });

export function useClaim(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<WorkItem>(`/work-items/${id}/claim`),
    onSuccess: (item) => reconcile(qc, item),
    onError: () => {
      void qc.invalidateQueries({ queryKey: ["item", id] });
      void qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

export function useTransition(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { to_status: Status; expected_version: number; resolution_note?: string }) =>
      post<WorkItem>(`/work-items/${id}/transitions`, v),
    onSuccess: (item) => reconcile(qc, item),
    onError: (err) => reconcileFromError(qc, id, err),
  });
}

export function useEditItem(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: Record<string, unknown> & { expected_version: number }) =>
      request<WorkItem>(`/work-items/${id}`, { method: "PATCH", body: v, idempotent: true }),
    onSuccess: (item) => reconcile(qc, item),
    onError: (err) => reconcileFromError(qc, id, err),
  });
}

export function useAddComment(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) => post<Comment>(`/work-items/${id}/comments`, { body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["comments", id] });
      void qc.invalidateQueries({ queryKey: ["item", id] });
    },
  });
}

export function useDecideApproval(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { decision: "approved" | "rejected"; note?: string }) =>
      post<WorkItem>(`/work-items/${id}/approval/decision`, v),
    onSuccess: (item) => reconcile(qc, item),
    onError: (err) => reconcileFromError(qc, id, err),
  });
}

export function useRequestApproval(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (approver_id: number) => post<WorkItem>(`/work-items/${id}/approval`, { approver_id }),
    onSuccess: (item) => reconcile(qc, item),
    onError: (err) => reconcileFromError(qc, id, err),
  });
}

export interface NewItem {
  title: string;
  description: string;
  type: ItemType;
  priority: Priority;
  owning_team_id: number;
  approver_id?: number;
}
export function useCreateItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: NewItem) => post<WorkItem>("/work-items", v),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["items"] }),
  });
}

/** On a 409 the server sends the current item (version conflicts) - adopt it, otherwise just refetch. */
function reconcileFromError(qc: QueryClient, id: number, err: unknown): void {
  if (err instanceof ApiError && err.isVersionConflict && err.current) {
    qc.setQueryData<ItemDetail>(["item", id], (old) => (old ? { ...old, ...err.current! } : old));
  }
  void qc.invalidateQueries({ queryKey: ["item", id] });
  void qc.invalidateQueries({ queryKey: ["items"] });
}
