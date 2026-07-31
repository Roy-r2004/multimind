/**
 * Country visuals for the Maps Census UI — an outline-map watermark tinted with
 * that country's flag colors. Ships a bundled `world-atlas` topology (no network
 * call at runtime); missing entries in either table simply skip the outline/tint
 * and the caller falls back to a plain gradient.
 */

// ISO 3166-1 numeric codes, keyed by alpha-2 — used to pick the right feature out
// of the `world-atlas` topology (whose feature `id` is the numeric code).
export const ISO_ALPHA2_TO_NUMERIC: Record<string, number> = {
  AF: 4, AL: 8, DZ: 12, AS: 16, AD: 20, AO: 24, AG: 28, AZ: 31, AR: 32, AU: 36,
  AT: 40, BS: 44, BH: 48, BD: 50, AM: 51, BB: 52, BE: 56, BT: 64, BO: 68, BA: 70,
  BW: 72, BV: 74, BR: 76, BZ: 84, SB: 90, BN: 96, BG: 100, MM: 104, BI: 108, BY: 112,
  KH: 116, CM: 120, CA: 124, CV: 132, CF: 140, LK: 144, TD: 148, CL: 152, CN: 156,
  CX: 162, CC: 166, CO: 170, KM: 174, CG: 178, CD: 180, CK: 184, CR: 188, HR: 191,
  CU: 192, CY: 196, CZ: 203, BJ: 204, DK: 208, DM: 212, DO: 214, EC: 218, SV: 222,
  GQ: 226, ET: 231, ER: 232, EE: 233, FO: 234, FJ: 242, FI: 246, FR: 250, GF: 254,
  PF: 258, DJ: 262, GA: 266, GE: 268, GM: 270, PS: 275, DE: 276, GH: 288, GI: 292,
  KI: 296, GR: 300, GL: 304, GD: 308, GP: 312, GU: 316, GT: 320, GN: 324, GY: 328,
  HT: 332, HM: 334, VA: 336, HN: 340, HK: 344, HU: 348, IS: 352, IN: 356, ID: 360,
  IR: 364, IQ: 368, IE: 372, IL: 376, IT: 380, JM: 388, JP: 392, KZ: 398, JO: 400,
  KE: 404, KP: 408, KR: 410, KW: 414, KG: 417, LA: 418, LB: 422, LS: 426, LV: 428,
  LR: 430, LY: 434, LI: 438, LT: 440, LU: 442, MO: 446, MG: 450, MW: 454, MY: 458,
  MV: 462, ML: 466, MT: 470, MH: 584, MQ: 474, MR: 478, MU: 480, MX: 484, FM: 583,
  MC: 492, MN: 496, MD: 498, ME: 499, MS: 500, MA: 504, MZ: 508, OM: 512, NA: 516,
  NR: 520, NP: 524, NL: 528, NC: 540, NZ: 554, NI: 558, NE: 562, NG: 566, NU: 570,
  NF: 574, MK: 807, MP: 580, NO: 578, PK: 586, PW: 585, PA: 591, PG: 598, PY: 600,
  PE: 604, PH: 608, PN: 612, PL: 616, PT: 620, PR: 630, QA: 634, RE: 638, RO: 642,
  RU: 643, RW: 646, BL: 652, SH: 654, KN: 659, LC: 662, MF: 663, PM: 666, VC: 670,
  WS: 882, SM: 674, ST: 678, SA: 682, SN: 686, RS: 688, SC: 690, SL: 694, SG: 702,
  SK: 703, SI: 705, SO: 706, ZA: 710, GS: 239, SS: 728, ES: 724, SD: 729, SR: 740,
  SJ: 744, SZ: 748, SE: 752, CH: 756, SY: 760, TW: 158, TJ: 762, TZ: 834, TH: 764,
  TL: 626, TG: 768, TK: 772, TO: 776, TT: 780, TN: 788, TR: 792, TM: 795, TC: 796,
  TV: 798, UG: 800, UA: 804, AE: 784, GB: 826, US: 840, UY: 858, UZ: 860, VU: 548,
  VE: 862, VN: 704, VG: 92, VI: 850, WF: 876, EH: 732, YE: 887, ZM: 894, ZW: 716,
  UM: 581, EG: 818, XK: 411,
};

// Two dominant flag colors per country, used to tint the outline watermark.
// Countries without an entry fall back to DEFAULT_FLAG_COLORS.
export const FLAG_COLORS: Record<string, [string, string]> = {
  AT: ["#ED2939", "#FFFFFF"],
  BY: ["#D22730", "#4AA657"],
  FI: ["#003580", "#FFFFFF"],
  LB: ["#ED1C24", "#00A651"],
  US: ["#B22234", "#3C3B6E"],
  GB: ["#C8102E", "#012169"],
  FR: ["#0055A4", "#EF4135"],
  DE: ["#000000", "#FFCC00"],
  IT: ["#009246", "#CE2B37"],
  ES: ["#AA151B", "#F1BF00"],
  PT: ["#046A38", "#DA291C"],
  NL: ["#AE1C28", "#21468B"],
  BE: ["#000000", "#FDDA24"],
  CH: ["#D52B1E", "#FFFFFF"],
  SE: ["#005293", "#FECC02"],
  NO: ["#BA0C2F", "#00205B"],
  DK: ["#C60C30", "#FFFFFF"],
  PL: ["#DC143C", "#FFFFFF"],
  CZ: ["#11457E", "#D7141A"],
  SK: ["#0B4EA2", "#EE1C25"],
  HU: ["#436F4D", "#CD2A3E"],
  RO: ["#002B7F", "#FCD116"],
  BG: ["#00966E", "#D62612"],
  GR: ["#0D5EAF", "#FFFFFF"],
  UA: ["#0057B7", "#FFD700"],
  RU: ["#0039A6", "#D52B1E"],
  TR: ["#E30A17", "#FFFFFF"],
  IE: ["#169B62", "#FF883E"],
  IS: ["#02529C", "#DC1E35"],
  EE: ["#0072CE", "#FFFFFF"],
  LV: ["#9E3039", "#FFFFFF"],
  LT: ["#FDB913", "#006A44"],
  HR: ["#FF0000", "#171796"],
  SI: ["#0000FF", "#FF0000"],
  RS: ["#C6363C", "#0C4076"],
  BA: ["#002395", "#FECB00"],
  ME: ["#D3AF37", "#C40308"],
  MK: ["#D20000", "#FFE600"],
  AL: ["#E41E20", "#000000"],
  MD: ["#0033A0", "#FFD200"],
  GE: ["#FF0000", "#FFFFFF"],
  AM: ["#D90012", "#0033A0"],
  AZ: ["#00B9E4", "#EF3340"],
  KZ: ["#00AFCA", "#FEC50C"],
  CA: ["#FF0000", "#FFFFFF"],
  MX: ["#006847", "#CE1126"],
  BR: ["#009B3A", "#FEDF00"],
  AR: ["#74ACDF", "#FFFFFF"],
  CL: ["#D52B1E", "#0039A6"],
  CO: ["#FCD116", "#003893"],
  PE: ["#D91023", "#FFFFFF"],
  VE: ["#FFD100", "#00247D"],
  EC: ["#FFD100", "#0072CE"],
  UY: ["#0038A8", "#FFFFFF"],
  PY: ["#D52B1E", "#0038A8"],
  BO: ["#D52B1E", "#007934"],
  CN: ["#DE2910", "#FFDE00"],
  JP: ["#BC002D", "#FFFFFF"],
  KR: ["#003478", "#C60C30"],
  KP: ["#024FA2", "#ED1C27"],
  IN: ["#FF9933", "#138808"],
  PK: ["#01411C", "#FFFFFF"],
  BD: ["#006A4E", "#F42A41"],
  LK: ["#FFBE29", "#8D153A"],
  NP: ["#DC143C", "#003893"],
  ID: ["#FF0000", "#FFFFFF"],
  MY: ["#010066", "#CC0001"],
  TH: ["#A51931", "#2D2A4A"],
  VN: ["#DA251D", "#FFFF00"],
  PH: ["#0038A8", "#CE1126"],
  MM: ["#FECB00", "#34B233"],
  KH: ["#032EA1", "#E00025"],
  LA: ["#CE1126", "#002868"],
  MN: ["#DA2032", "#015197"],
  AF: ["#000000", "#D32011"],
  IR: ["#239F40", "#DA0000"],
  IQ: ["#CE1126", "#000000"],
  SY: ["#CE1126", "#000000"],
  IL: ["#0038B8", "#FFFFFF"],
  PS: ["#CE1126", "#007A3D"],
  JO: ["#007A3D", "#CE1126"],
  SA: ["#006C35", "#FFFFFF"],
  AE: ["#00732F", "#FF0000"],
  QA: ["#8D1B3D", "#FFFFFF"],
  KW: ["#007A3D", "#CE1126"],
  BH: ["#CE1126", "#FFFFFF"],
  OM: ["#DB161B", "#008751"],
  YE: ["#CE1126", "#000000"],
  EG: ["#CE1126", "#000000"],
  LY: ["#E70013", "#239E46"],
  TN: ["#E70013", "#FFFFFF"],
  DZ: ["#006233", "#D21034"],
  MA: ["#C1272D", "#006233"],
  SD: ["#D21034", "#007229"],
  ET: ["#078930", "#FCDD09"],
  KE: ["#BB0000", "#006600"],
  TZ: ["#1EB53A", "#00A3DD"],
  UG: ["#000000", "#FCDC04"],
  NG: ["#008751", "#FFFFFF"],
  GH: ["#CE1126", "#FCD116"],
  SN: ["#00853F", "#FDEF42"],
  CI: ["#F77F00", "#009E60"],
  CM: ["#007A5E", "#CE1126"],
  ZA: ["#007A4D", "#DE3831"],
  ZW: ["#006400", "#FFD200"],
  ZM: ["#198A00", "#DE2010"],
  NA: ["#003580", "#D21034"],
  BW: ["#75AADB", "#000000"],
  MZ: ["#009639", "#CE1126"],
  AO: ["#CC092F", "#000000"],
  CD: ["#007FFF", "#F7D618"],
  RW: ["#00A1DE", "#FAD201"],
  SO: ["#4189DD", "#FFFFFF"],
  AU: ["#00008B", "#FFFFFF"],
  NZ: ["#00247D", "#CC142B"],
  FJ: ["#68BFE5", "#FFFFFF"],
  PG: ["#000000", "#CE1126"],
};

export const DEFAULT_FLAG_COLORS: [string, string] = ["#0d9488", "#0ea5e9"];

export function getFlagColors(countryCode: string): [string, string] {
  return FLAG_COLORS[countryCode.toUpperCase()] ?? DEFAULT_FLAG_COLORS;
}

/** Unicode regional-indicator flag emoji for a 2-letter country code — no asset needed. */
export function countryFlagEmoji(countryCode: string): string {
  const code = countryCode.toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return "🏳️";
  const codePoints = [...code].map((char) => 0x1f1e6 + (char.charCodeAt(0) - 65));
  return String.fromCodePoint(...codePoints);
}

type Topology = {
  type: "Topology";
  objects: Record<string, unknown>;
  arcs: number[][][];
};

type OutlineFeature = {
  id?: string | number;
  type: string;
  properties?: Record<string, unknown>;
  geometry: unknown;
};

let topologyPromise: Promise<Topology> | null = null;

async function loadTopology(): Promise<Topology> {
  if (!topologyPromise) {
    topologyPromise = import("world-atlas/countries-50m.json").then(
      (mod) => (mod as unknown as { default: Topology }).default ?? (mod as unknown as Topology),
    );
  }
  return topologyPromise;
}

const outlinePathCache = new Map<string, string | null>();

/**
 * SVG path `d` string for a country's outline, fit into a 200x200 viewBox.
 * Returns null when the country code has no numeric mapping or no matching
 * feature — callers should fall back to a plain gradient in that case.
 */
export async function getCountryOutlinePath(countryCode: string): Promise<string | null> {
  const code = countryCode.toUpperCase();
  if (outlinePathCache.has(code)) {
    return outlinePathCache.get(code) ?? null;
  }
  const numericId = ISO_ALPHA2_TO_NUMERIC[code];
  if (numericId === undefined) {
    outlinePathCache.set(code, null);
    return null;
  }

  const [topology, { feature }, { geoMercator, geoPath }] = await Promise.all([
    loadTopology(),
    import("topojson-client"),
    import("d3-geo"),
  ]);

  const countriesObject = topology.objects.countries;
  if (!countriesObject) {
    outlinePathCache.set(code, null);
    return null;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const collection = feature(topology as any, countriesObject as any) as unknown as {
    features: OutlineFeature[];
  };
  const match = collection.features.find((item) => Number(item.id) === numericId);
  if (!match) {
    outlinePathCache.set(code, null);
    return null;
  }

  const projection = geoMercator().fitSize([200, 200], match as never);
  const pathGenerator = geoPath(projection);
  const d = pathGenerator(match as never);
  outlinePathCache.set(code, d);
  return d;
}
