import { handlers } from "@/auth";
import { cleanAuthResponseCookies } from "@/lib/authCookies";

export async function GET(request: Parameters<typeof handlers.GET>[0]) {
  return cleanAuthResponseCookies(request, await handlers.GET(request));
}

export async function POST(request: Parameters<typeof handlers.POST>[0]) {
  return cleanAuthResponseCookies(request, await handlers.POST(request));
}
