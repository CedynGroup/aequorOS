"use client";

import { useEffect } from "react";

const destinations: Record<string, string> = {
  members: "/access/members",
  grants: "/access/members",
  users: "/access/members",
  authentication: "/access/authentication",
  auth: "/access/authentication",
  sso: "/access/authentication",
  "access-requests": "/access/authentication",
  "integration-keys": "/access/integration-keys",
  "api-keys": "/access/integration-keys",
  "machine-principals": "/access/integration-keys",
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
