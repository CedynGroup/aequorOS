"use client";

import { useEffect } from "react";

const destinations: Record<string, string> = {
  members: "/access/members",
  authentication: "/access/authentication",
  "integration-keys": "/access/integration-keys",
};

export default function LegacyAccessAnchorRedirect() {
  useEffect(() => {
    const redirect = () => {
      const anchor = window.location.hash.slice(1).toLowerCase();
      const destination = destinations[anchor];
      if (destination) window.location.replace(destination);
    };
    redirect();
    window.addEventListener("hashchange", redirect);
    return () => window.removeEventListener("hashchange", redirect);
  }, []);
  return null;
}
