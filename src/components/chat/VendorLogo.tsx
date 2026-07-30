/** Compact vendor marks for council / model-set cards. */

import { cn } from "@/lib/utils";
import { resolveVendorMarkId, type VendorMarkId } from "@/lib/vendorMarks";
import openai from "@/assets/vendors/openai.svg";
import anthropic from "@/assets/vendors/anthropic.svg";
import google from "@/assets/vendors/google.svg";
import xai from "@/assets/vendors/xai.svg";
import deepseek from "@/assets/vendors/deepseek.svg";
import mistral from "@/assets/vendors/mistral.svg";
import meta from "@/assets/vendors/meta.svg";
import alibaba from "@/assets/vendors/alibaba.svg";
import fallback from "@/assets/vendors/default.svg";

type Props = {
  vendor: string;
  className?: string;
  watermark?: boolean;
  title?: string;
};

const MARK_SRC: Record<VendorMarkId, string> = {
  openai,
  anthropic,
  google,
  xai,
  deepseek,
  mistral,
  meta,
  alibaba,
  default: fallback,
};

export function VendorLogo({ vendor, className, watermark, title }: Props) {
  const markId = resolveVendorMarkId(vendor);
  const src = MARK_SRC[markId];

  return (
    <span
      className={cn(
        "inline-grid place-items-center overflow-hidden rounded-full",
        watermark ? "opacity-10" : "ring-1 ring-black/10",
        className,
      )}
      title={title}
      aria-hidden={!title}
    >
      <img
        src={src}
        alt=""
        draggable={false}
        className={cn("size-[92%] object-contain", watermark && "size-full")}
      />
    </span>
  );
}
