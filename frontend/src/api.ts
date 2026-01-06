export type MatchType = "fut5" | "fut6" | "fut7";

export type MatchListItem = {
  id: number;
  title: string;
  matchType: MatchType;
  capacity: number;
  isOpen: boolean;
  scheduledAt: string | null;
  createdAt: string;
  joinCode: string;
  countActive: number;
};

export type Signup = {
  id: number;
  playerId: number;
  playerName: string;
  isPaid: boolean;
  isCancelled: boolean;
  notes: string | null;
  joinedAt: string;
};

export type MatchDetail = MatchListItem & {
  signups: Signup[];
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      msg = data?.detail ?? msg;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }

  return (await res.json()) as T;
}

export async function listMatches(): Promise<MatchListItem[]> {
  return apiFetch<MatchListItem[]>("/api/matches");
}

export async function createMatch(input: {
  title: string;
  matchType: MatchType;
  scheduledAt?: string;
}): Promise<MatchListItem> {
  return apiFetch<MatchListItem>("/api/matches", {
    method: "POST",
    body: JSON.stringify({
      title: input.title,
      matchType: input.matchType,
      scheduledAt: input.scheduledAt ?? null,
    }),
  });
}

export async function getMatch(matchId: number): Promise<MatchDetail> {
  return apiFetch<MatchDetail>(`/api/matches/${matchId}`);
}

export async function toggleOpen(matchId: number): Promise<{ id: number; isOpen: boolean }> {
  return apiFetch(`/api/matches/${matchId}/toggle-open`, { method: "POST" });
}

export async function addSignup(matchId: number, input: { name: string; notes?: string }): Promise<Signup> {
  return apiFetch(`/api/matches/${matchId}/signups`, {
    method: "POST",
    body: JSON.stringify({ name: input.name, notes: input.notes ?? null }),
  });
}

export async function patchSignup(
  signupId: number,
  patch: { isPaid?: boolean; isCancelled?: boolean; notes?: string | null }
): Promise<Signup> {
  return apiFetch(`/api/signups/${signupId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export type AnalyticsRow = {
  playerId: number;
  name: string;
  total: number;
  cancelRate: number;
  payRate: number;
  risk: number;
};

export async function getAnalytics(): Promise<AnalyticsRow[]> {
  return apiFetch("/api/analytics");
}

