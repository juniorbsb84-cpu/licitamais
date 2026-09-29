import { useQuery } from "@tanstack/react-query";
import { api, definirCsrf } from "./cliente";
import type { components } from "./esquema";

export type Me = components["schemas"]["Conta"];

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await api<Me>("/me");
      definirCsrf(me.csrf);
      return me;
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}
