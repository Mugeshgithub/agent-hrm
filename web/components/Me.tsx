"use client";
import { createContext, useContext } from "react";
import { Person } from "@/lib/types";

export const MeContext = createContext<Person | null>(null);

export function useMe() {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe outside the app shell");
  return me;
}
