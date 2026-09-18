"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  KeyRound,
  Plus,
  ShieldCheck,
  X,
} from "lucide-react";
import { signOut } from "next-auth/react";
import type {
  BindingCreateRequest,
  BindingCreateResponse,
  BindingRead,
  AccessRequestRead,
  MemberRead,
} from "@aequoros/risk-service-api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { SkeletonLine } from "@/components/ui/Skeleton";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { authApi, authorizationApi, normalizeApiError } from "@/lib/api/client";
import { loginUrlWithReason } from "@/lib/loginUrl";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { avatarColor, initialsFrom } from "@/lib/api/identity";
import { fmtRelative } from "@/lib/api/values";
import {
  ORGANIZATION_MEMBERS_QUERY_KEY,
  useGrantableInstitutions,
} from "@/lib/api/grantAdministration";
import {
  canAddGrantToMember,
  MODULE_OPTIONS,
  ROLE_OPTIONS,
  SENSITIVITY_OPTIONS,
  visibleGrantFragments,
  type GrantDraft,
} from "@/lib/api/grants";
import {
  grantShortfall,
  overlappingGrantNotice,
} from "@/lib/api/grantRequirements";
import { sodFindings, sodRemedy, type SodFinding } from "@/lib/api/sodDecision";
import {
  GrantReasonFields,
  REASON_OPTIONS,
  reasonLabel,
} from "@/components/access/GrantReasonFields";
