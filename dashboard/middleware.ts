import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PREFIXES = ["/login", "/miniapp", "/api", "/_next", "/favicon.ico"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PREFIXES.some(
      (p) => pathname === p || pathname.startsWith(p + "/") || pathname.startsWith(p)
    )
  ) {
    // /api is rewritten by next.config — allow through
    if (pathname.startsWith("/api") || pathname.startsWith("/_next") || pathname.startsWith("/miniapp")) {
      return NextResponse.next();
    }
    if (pathname === "/login" || pathname.startsWith("/login/")) {
      const token = request.cookies.get("access_token")?.value;
      if (token) {
        return NextResponse.redirect(new URL("/", request.url));
      }
      return NextResponse.next();
    }
  }

  // Static assets
  if (pathname.match(/\.(ico|png|jpg|jpeg|svg|gif|webp|css|js|woff2?)$/)) {
    return NextResponse.next();
  }

  const token = request.cookies.get("access_token")?.value;
  if (!token) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
