import type { ApiBrain, ApiLessonDetail, ApiLessonListItem } from "@/lib/api/types";

export const MOCK_BRAIN: ApiBrain = {
  user_name: "Chafic Challita",
  summary:
    "Chafic optimizes for speed and market capture when he has a distribution edge. He weighs long-term brand risk but will trade margin for share if the competitive window is narrow.",
  thinking_style:
    "First principles → competitive dynamics → execution timeline. Skeptical of 'wait and see' when momentum matters.",
  likes: [
    "Volume-first pricing when distribution is the moat",
    "Concrete next steps over abstract frameworks",
    "Debate-style verdicts that show both sides",
    "90-day decision horizons",
  ],
  dislikes: [
    "Margin-only optimization without market context",
    "Vague 'it depends' without a default call",
    "Over-indexing on worst-case scenarios",
    "Enterprise jargon without numbers",
  ],
  lesson_count: 3,
  updated_at: new Date().toISOString(),
  memories: [
    {
      id: "mock-pricing",
      source: "lesson",
      source_id: "mock-pricing",
      title: "Pricing: volume vs margin",
      insight: "Favors penetration pricing when retail partnerships are live.",
      likes: ["Land-and-expand pricing"],
      dislikes: ["Premium-first without channel proof"],
      created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    },
    {
      id: "mock-hiring",
      source: "lesson",
      source_id: "mock-hiring",
      title: "Hiring: senior vs build internally",
      insight: "One anchor hire beats a large junior cohort when the roadmap is ambiguous.",
      likes: ["Anchor hire + small team"],
      dislikes: ["Headcount before product clarity"],
      created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
    },
    {
      id: "mock-gtm",
      source: "lesson",
      source_id: "mock-gtm",
      title: "Go-to-market: direct vs partners",
      insight: "Partner-led GTM for speed, with a direct channel path within two quarters.",
      likes: ["Hybrid GTM with exit ramp"],
      dislikes: ["Permanent partner dependency"],
      created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
    },
  ],
};

export const MOCK_LESSONS: ApiLessonListItem[] = [
  {
    id: "mock-pricing",
    turn_id: "turn-1",
    chat_id: "chat-1",
    title: "Pricing strategy: volume vs margin",
    summary:
      "The verdict pushed premium pricing; Chafic argued penetration pricing wins when shelf space is secured.",
    user_name: "Chafic Challita",
    verdict_model_name: "GPT-4.1",
    status: "completed",
    created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "mock-hiring",
    turn_id: "turn-2",
    chat_id: "chat-2",
    title: "Hiring plan: scale team vs anchor hire",
    summary:
      "Models recommended a 6-person squad; Chafic wanted one senior lead first while scope stabilizes.",
    user_name: "Chafic Challita",
    verdict_model_name: "Claude Sonnet 4",
    status: "completed",
    created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
  },
  {
    id: "mock-gtm",
    turn_id: "turn-3",
    chat_id: "chat-3",
    title: "GTM: direct sales vs retail partners",
    summary:
      "Verdict favored direct-only GTM; Chafic prioritized partner velocity with a planned direct pivot.",
    user_name: "Chafic Challita",
    verdict_model_name: "Gemini 2.5 Pro",
    status: "completed",
    created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
  },
];

const PRICING_LESSON: ApiLessonDetail = {
  ...MOCK_LESSONS[0],
  user_message: "Should we launch at $49/mo premium or $29/mo to maximize retail partner uptake?",
  disagreement_reason:
    "The verdict ignored our signed retail pipeline. Premium pricing kills velocity on shelf — we have 90 days before competitors copy us.",
  user_position:
    "Launch at $29 with annual upsell. Capture share now, raise prices after NPS and retention prove out in Q3.",
  verdict_model_id: "gpt-4.1",
  verdict_text:
    "Recommend $49/mo premium positioning to protect margin and brand perception. Discounting early trains customers to expect low prices.",
  verdict_reason: "Synthesized conservative financial framing from three models; weighted margin protection.",
  strategy: "Debate",
  comparison: {
    overview:
      "Core tension: short-term distribution velocity vs long-term margin and brand premium. Chafic optimizes for window-of-opportunity; the verdict optimizes for unit economics at steady state.",
    user_position_summary: "Penetration pricing wins when shelf space and partner momentum are the scarce assets.",
    model_position_summary: "Premium pricing protects margin and avoids a painful future price increase.",
    agreements: [
      { topic: "Brand matters", detail: "Both sides want ACME perceived as quality, not a budget tool." },
      { topic: "Retail partners are key", detail: "Neither disputes that partner shelf is the primary Q1 channel." },
    ],
    disagreements: [
      {
        topic: "Price point at launch",
        user_view: "$29 drives trial volume and partner sell-through metrics.",
        model_view: "$49 establishes value and funds support costs.",
        analysis: "Disagreement is really about time horizon — 90-day land grab vs 12-month margin curve.",
      },
      {
        topic: "Risk of low-price anchoring",
        user_view: "Annual plans and tiered upsell reset willingness-to-pay.",
        model_view: "Customers anchor on launch price; upgrades are harder than models predict.",
        analysis: "Chafic accepts re-pricing friction if share is secured before copycats arrive.",
      },
    ],
    evidence: [
      {
        claim: "Partner sell-through targets",
        user_evidence: "Signed LOIs require sub-$35 price band for co-marketing.",
        model_evidence: "Comparable SaaS launches at $49+ with similar feature sets.",
        assessment: "User evidence is situational and stronger for this specific pipeline.",
      },
    ],
    assumptions: {
      user: ["Competitive window closes in ~90 days", "Partners won't renegotiate if metrics hit"],
      model: ["Market tolerates premium without trial friction", "Support costs scale linearly with users"],
    },
    blind_spots: {
      user: ["Support load at $29 may compress margin more than forecast"],
      model: ["Underestimates partner-driven discovery vs paid acquisition"],
    },
    lesson: {
      headline: "When distribution is the moat, price for velocity first",
      key_insight: "Chafic treats pricing as a GTM lever, not only a margin lever.",
      what_to_remember: [
        "Ask 'what do we lose by waiting?' before accepting premium-first advice",
        "Frame pricing decisions with partner constraints explicit",
      ],
      when_user_might_be_right: "Signed channel, narrow competitive window, measurable sell-through KPIs.",
      when_model_might_be_right: "Organic demand, high support burden, weak competitive pressure.",
      recommended_next_step: "Run 30-day A/B on partner subset at $29 vs $39 before global launch.",
    },
  },
  error_message: null,
};

export const MOCK_LESSON_BY_ID: Record<string, ApiLessonDetail> = {
  "mock-pricing": PRICING_LESSON,
  "mock-hiring": {
    ...MOCK_LESSONS[1],
    user_message: "Should we hire 6 mid-level engineers now or one staff engineer and grow slowly?",
    disagreement_reason: "The verdict treated headcount as progress. We're still pivoting the roadmap weekly.",
    user_position: "Hire one staff engineer who has shipped 0→1. Add juniors only after architecture stabilizes.",
    verdict_model_id: "claude",
    verdict_text: "Build a balanced 6-person pod for parallel workstreams across frontend, backend, and infra.",
    verdict_reason: "Pick Best strategy favored the model with strongest engineering org design argument.",
    strategy: "Pick Best",
    comparison: {
      overview: "Team shape vs roadmap certainty.",
      user_position_summary: "Anchor hire reduces coordination tax during ambiguity.",
      model_position_summary: "Parallel workstreams need breadth of ownership.",
      agreements: [{ topic: "Quality bar", detail: "Both want senior review on architecture." }],
      disagreements: [
        {
          topic: "Team size",
          user_view: "Small senior team ships faster pre-PMF.",
          model_view: "Six engineers cover more surface area.",
          analysis: "Depends on roadmap volatility — high volatility favors Chafic.",
        },
      ],
      evidence: [],
      assumptions: { user: ["Roadmap changes weekly"], model: ["Workstreams are independent"] },
      blind_spots: { user: ["Bus factor on single staff engineer"], model: ["Coordination overhead of six"] },
      lesson: {
        headline: "Match headcount to roadmap entropy",
        key_insight: "Chafic hires for decision quality before hiring for throughput.",
        what_to_remember: ["Anchor + small team when scope is unstable"],
        when_user_might_be_right: "Pre-PMF, weekly pivots, architecture unsettled.",
        when_model_might_be_right: "Clear parallel tracks, stable spec, hard deadlines.",
        recommended_next_step: "Staff engineer trial project for 2 weeks before opening other reqs.",
      },
    },
    error_message: null,
  },
  "mock-gtm": {
    ...MOCK_LESSONS[2],
    user_message: "Go direct-only sales or lead with retail partners for launch?",
    disagreement_reason: "Direct sales won't hit shelf velocity targets in Q1.",
    user_position: "Partners now, direct in Q3 once we have case studies and support playbooks.",
    verdict_model_id: "gemini",
    verdict_text: "Own the customer relationship from day one — build direct sales motion immediately.",
    verdict_reason: "Debate strategy; direct margin and feedback loop won the synthesis.",
    strategy: "Debate",
    comparison: {
      overview: "Channel speed vs customer ownership.",
      user_position_summary: "Partners are a distribution accelerant with a planned direct transition.",
      model_position_summary: "Direct feedback and margin require owning the relationship early.",
      agreements: [{ topic: "Customer feedback", detail: "Both want faster learning loops." }],
      disagreements: [
        {
          topic: "Launch channel",
          user_view: "Retail partners deliver volume Chafic can't replicate direct in 90 days.",
          model_view: "Direct avoids partner margin share and misaligned incentives.",
          analysis: "Hybrid with explicit exit ramp satisfies both if milestones are defined.",
        },
      ],
      evidence: [],
      assumptions: { user: ["Partners hit volume targets"], model: ["Direct can scale fast enough"] },
      blind_spots: { user: ["Partner dependency risk"], model: ["Time-to-shelf underestimated"] },
      lesson: {
        headline: "Hybrid GTM with a written direct ramp",
        key_insight: "Chafic accepts partner margin cost as speed insurance.",
        what_to_remember: ["Always define when and how you go direct"],
        when_user_might_be_right: "Hard retail deadlines, limited sales headcount.",
        when_model_might_be_right: "High-touch product needing deep discovery.",
        recommended_next_step: "Contractual Q3 direct option with top partner tier.",
      },
    },
    error_message: null,
  },
};

export function getMockLesson(id: string): ApiLessonDetail | undefined {
  return MOCK_LESSON_BY_ID[id];
}
