"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import { hrefAccess, type HrefAccess } from "@/lib/modules";
import { useModuleScope } from "./BankContext";
import {
  Search,
  LayoutDashboard,
  Gauge,
  BellRing,
  CandlestickChart,
  Layers,
  Activity,
  Database,
  Droplet,
  DollarSign,
  ShieldCheck,
  GitBranch,
  TrendingUp,
  BrainCircuit,
  FileBarChart2,
  FileCheck2,
  Settings,
  ArrowRight,
  BookOpenCheck,
  ClipboardCheck,
  UserRoundCog,
