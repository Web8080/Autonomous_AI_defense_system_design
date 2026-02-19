import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname;

  if (path === "/" || path === "") {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  if (path === "/simulation" || path === "/simulations") {
    return NextResponse.redirect(new URL("/dashboard/simulation", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/", "/simulation", "/simulations"],
};
