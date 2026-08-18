import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const W = 1280;
const H = 720;
const C = {
  orange: "#FD9F28",
  gray: "#EEEEEE",
  brown: "#401600",
  black: "#000000",
  white: "#FFFFFF",
};
const FONT = "NanumSquare";
const ROOT = "C:/dayjaview/deliverables/dayjaview_slides";
const SVG_DIR = path.join(ROOT, "DAY-JA-VIEW_SVG");
const PREVIEW_DIR = path.join(ROOT, "previews");
const SOURCE_NOTE = "C:/Users/KDA 6/Downloads/kda 2차 대본.md";
const FONT_SOURCE = "https://github.com/moonspam/NanumSquare";

const text = (x, y, w, h, value, size = 24, color = C.black, weight = 400, align = "left", vAlign = "middle", extra = {}) => ({
  type: "text", x, y, w, h, value, size, color, weight, align, vAlign, ...extra,
});
const rect = (x, y, w, h, fill = C.white, radius = 0, stroke = "none", strokeWidth = 0, extra = {}) => ({
  type: "rect", x, y, w, h, fill, radius, stroke, strokeWidth, ...extra,
});
const line = (x1, y1, x2, y2, stroke = C.orange, strokeWidth = 2, extra = {}) => ({
  type: "line", x1, y1, x2, y2, stroke, strokeWidth, ...extra,
});
const circle = (cx, cy, r, fill = C.orange, stroke = "none", strokeWidth = 0, extra = {}) => ({
  type: "circle", cx, cy, r, fill, stroke, strokeWidth, ...extra,
});
const arrow = (x, y, w, h, fill = C.orange, extra = {}) => ({ type: "arrow", x, y, w, h, fill, ...extra });

function header(parts, section, title, no) {
  parts.push(rect(68, 46, 7, 68, C.brown));
  parts.push(text(92, 43, 650, 28, section, 15, C.orange, 800, "left", "middle"));
  parts.push(text(92, 70, 1030, 48, title, 32, C.brown, 800));
  parts.push(text(1170, 47, 42, 28, String(no).padStart(2, "0"), 14, C.orange, 800, "right"));
}

function footer(parts, label = "DAY-JA-VIEW") {
  parts.push(line(68, 684, 1212, 684, C.gray, 2));
  parts.push(text(68, 688, 240, 20, label, 11, C.brown, 700, "left", "middle", { letterSpacing: 1.2 }));
}

function pill(parts, x, y, w, label, fill = C.orange, color = C.white, size = 17) {
  parts.push(rect(x, y, w, 42, fill, 21));
  parts.push(text(x + 10, y, w - 20, 42, label, size, color, 800, "center"));
}

function card(parts, x, y, w, h, titleValue, bodyLines, options = {}) {
  const fill = options.fill ?? C.white;
  const stroke = options.stroke ?? C.gray;
  parts.push(rect(x, y, w, h, fill, options.radius ?? 18, stroke, options.strokeWidth ?? 2));
  if (options.tag) pill(parts, x + 22, y + 20, options.tagWidth ?? 96, options.tag, options.tagFill ?? C.orange, options.tagColor ?? C.white, 14);
  parts.push(text(x + 24, y + (options.tag ? 70 : 22), w - 48, 40, titleValue, options.titleSize ?? 22, options.titleColor ?? C.brown, 800));
  parts.push(text(x + 24, y + (options.tag ? 116 : 72), w - 48, h - (options.tag ? 132 : 88), bodyLines, options.bodySize ?? 18, options.bodyColor ?? C.black, 400, "left", "top", { lineHeight: options.lineHeight ?? 28 }));
}

function dotBullet(parts, x, y, lines, options = {}) {
  parts.push(circle(x, y + 12, 5, options.dotColor ?? C.orange));
  parts.push(text(x + 18, y, options.w ?? 420, options.h ?? 58, lines, options.size ?? 18, options.color ?? C.black, options.weight ?? 400, "left", "top", { lineHeight: options.lineHeight ?? 27 }));
}

const slides = [];
function addSlide(titleValue, parts, notes) {
  slides.push({ title: titleValue, parts, notes });
}

// 01. Service naming / hook
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  p.push(text(68, 58, 260, 26, "SERVICE INTRO", 15, C.orange, 800, "left", "middle", { letterSpacing: 1.6 }));
  p.push(text(68, 144, 660, 136, ["“이거, 예전에도", "비슷한 일이 있었던 것 같은데?”"], 47, C.brown, 800, "left", "middle", { lineHeight: 62 }));
  p.push(line(68, 318, 642, 318, C.orange, 4));
  p.push(text(68, 350, 610, 72, ["오늘의 테마와 과거의 유사 사건을 연결하는", "테마 이벤트 스터디 툴"], 23, C.black, 400, "left", "top", { lineHeight: 34 }));
  p.push(text(68, 500, 650, 62, "DAY-JA-VIEW", 52, C.orange, 800, "left", "middle", { letterSpacing: 1.5 }));
  pill(p, 69, 578, 138, "DAY · 그날", C.brown, C.white, 16);
  pill(p, 219, 578, 156, "VIEW · 반응", C.orange, C.white, 16);

  p.push(circle(986, 342, 212, C.gray));
  p.push(circle(986, 342, 158, C.white, C.brown, 4));
  p.push(circle(986, 342, 104, C.white, C.orange, 8));
  p.push(circle(986, 342, 42, C.brown));
  p.push(circle(880, 224, 15, C.orange));
  p.push(circle(1112, 250, 11, C.brown));
  p.push(circle(1134, 430, 18, C.orange));
  p.push(circle(886, 466, 10, C.brown));
  p.push(text(916, 317, 140, 50, "TODAY", 20, C.white, 800, "center"));
  p.push(text(795, 174, 380, 30, "PAST EVENTS ↔ TODAY", 14, C.brown, 800, "center", "middle", { letterSpacing: 1.3 }));
  footer(p);
  addSlide("DAY-JA-VIEW", p,
    "여러분도 시장을 보면서, ‘이거 예전에도 비슷한 일이 있었던 것 같은데?’라고 느낀 적 있으실 겁니다. 바로 데자뷰입니다.\n\n저희는 이런 경험을 투자 분석 과정에 적용했습니다. 오늘이자 과거 사건이 발생했던 ‘그날’을 뜻하는 DAY, 그리고 그날의 시장 반응을 보여주는 VIEW를 결합해 DAY-JA-VIEW라는 이름을 만들었습니다.");
}

// 02. Service flow
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "SERVICE", "흩어진 탐색을 하나의 분석 흐름으로", 2);
  p.push(text(92, 133, 950, 34, "발견 → 원인 확인 → 과거 비교", 22, C.black, 400));
  p.push(line(116, 286, 1164, 286, C.orange, 3));
  const xs = [220, 640, 1060];
  const items = [
    ["01", "DISCOVER", "오늘 무엇이", "움직이는가?", "강해지는 테마와", "주도 종목을 발견"],
    ["02", "UNDERSTAND", "왜", "움직이는가?", "관련 뉴스와 소재를", "같은 화면에서 확인"],
    ["03", "STUDY", "과거에는 어떻게", "반응했는가?", "유사 Event와", "T+1·5·20 결과 비교"],
  ];
  items.forEach((it, i) => {
    const x = xs[i];
    p.push(circle(x, 286, 13, C.orange));
    p.push(text(x - 100, 202, 200, 30, `${it[0]}  ${it[1]}`, 18, i === 1 ? C.brown : C.orange, 800, "center"));
    p.push(text(x - 128, 317, 256, 66, [it[2], it[3]], 20, C.brown, 700, "center", "middle", { lineHeight: 29 }));
    p.push(rect(x - 138, 425, 276, 104, i === 1 ? C.orange : C.brown, 18));
    p.push(text(x - 118, 425, 236, 104, [it[4], it[5]], 18, C.white, 700, "center", "middle", { lineHeight: 28 }));
  });
  p.push(text(260, 585, 760, 34, "사용자가 직접 연결하던 정보를 하나의 Event 단위로", 22, C.black, 400, "center"));
  p.push(text(260, 618, 760, 34, "발견부터 비교까지 끊김 없이 연결합니다.", 24, C.orange, 800, "center"));
  footer(p);
  addSlide("서비스 전체 흐름", p,
    "앞서 저희는 테마와 상승 소재, 실제 시장 반응을 하나의 Event로 연결하는 해결책을 제시했습니다. DAY-JA-VIEW에서는 이를 현재 강해지는 테마를 탐지하고, 상승 소재를 확인한 뒤, 비슷한 과거 Event와 당시의 실제 시장 반응까지 이어서 볼 수 있는 서비스로 구현했습니다. 즉, 사용자가 직접 흩어진 정보를 연결하던 과정을 ‘오늘의 움직임 발견, 원인 확인, 과거와 비교’라는 하나의 분석 흐름으로 만들었습니다.");
}

// 03. Demo video
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "DEMO", "서비스 시연", 3);
  p.push(rect(68, 150, 1144, 456, C.brown, 26));
  p.push(circle(640, 328, 60, C.orange));
  p.push({ type: "triangle", points: [[625, 294], [625, 362], [674, 328]], fill: C.white });
  p.push(text(168, 427, 944, 36, "오늘의 테마를 발견하고 · 소재를 이해하고 · 과거 반응을 비교하는 흐름", 22, C.white, 700, "center"));
  const beats = [["01", "실시간 테마"], ["02", "상승 소재"], ["03", "데자뷰 분석"]];
  beats.forEach((b, i) => {
    const x = 270 + i * 370;
    p.push(circle(x, 534, 22, C.orange));
    p.push(text(x - 22, 512, 44, 44, b[0], 14, C.white, 800, "center"));
    p.push(text(x + 38, 516, 180, 36, b[1], 18, C.white, 700));
  });
  p.push(text(68, 626, 1144, 30, "영상 재생 전: 세 장면만 안내 · 영상 재생 중: 설명 없이 화면에 집중", 15, C.brown, 400, "center"));
  footer(p);
  addSlide("서비스 시연", p,
    "이제 DAY-JA-VIEW가 실제로 어떻게 동작하는지 영상으로 보시겠습니다. 오늘 움직이는 테마를 발견하고, 상승 소재를 확인한 뒤, 비슷했던 과거 Event와 당시 시장 반응을 비교하는 순서입니다.\n\n[영상 재생 — 재생 중에는 말하지 않음]\n\n방금 보신 것처럼, 테마 탐색과 소재 확인, 과거 비교가 하나의 흐름 안에서 이어집니다.");
}

// 04. Feature 1
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "FEATURE 01 · DISCOVER", "테마를 발견한 화면에서 상승 소재까지", 4);
  p.push(text(92, 128, 920, 34, "MTS 밖으로 나가던 정보 탐색을 서비스 안에서 끝냅니다.", 22, C.black, 400));

  p.push(rect(78, 205, 470, 342, C.gray, 22));
  p.push(text(110, 226, 400, 38, "기존 탐색", 21, C.brown, 800));
  const old = [["MTS", 130], ["포털 뉴스", 270], ["텔레그램", 410]];
  old.forEach((item, i) => {
    p.push(rect(item[1], 302 + (i % 2) * 96, 124, 58, C.white, 16, C.brown, 2));
    p.push(text(item[1], 302 + (i % 2) * 96, 124, 58, item[0], 16, C.brown, 700, "center"));
    if (i < old.length - 1) p.push(arrow(item[1] + 130, 317 + (i % 2) * 96, 40, 28, C.orange));
  });
  p.push(rect(200, 452, 220, 58, C.white, 16, C.brown, 2));
  p.push(text(200, 452, 220, 58, "종목 커뮤니티", 16, C.brown, 700, "center"));
  p.push(text(110, 516, 400, 24, "화면 이동 · 맥락 단절 · 시간 소모", 14, C.brown, 700, "center"));

  p.push(arrow(570, 342, 70, 48, C.orange));

  p.push(rect(670, 205, 532, 342, C.brown, 22));
  p.push(text(704, 226, 460, 38, "DAY-JA-VIEW", 21, C.white, 800));
  const now = [
    ["강해지는 테마", "실시간 탐지"],
    ["상승 뉴스·소재", "원인 연결"],
    ["주도주·확산", "반응 확인"],
  ];
  now.forEach((n, i) => {
    const y = 296 + i * 78;
    p.push(circle(726, y + 24, 10, C.orange));
    p.push(text(752, y, 220, 48, n[0], 18, C.white, 800));
    pill(p, 1012, y + 4, 132, n[1], C.orange, C.white, 14);
  });
  p.push(line(704, 489, 1164, 489, C.orange, 2));
  p.push(text(704, 497, 460, 36, "소재 찾는 시간은 줄이고, 판단 시간은 늘립니다.", 18, C.white, 700, "center"));
  p.push(rect(258, 585, 764, 54, C.orange, 27));
  p.push(text(278, 585, 724, 54, "무엇이 움직이는지부터 왜 움직이는지까지, 한 화면에서", 20, C.white, 800, "center"));
  footer(p);
  addSlide("실시간 테마 탐지와 소재 확인", p,
    "첫 번째는 실시간 테마 탐지와 소재 확인입니다. MTS에서도 현재 강한 테마와 상승 종목은 확인할 수 있습니다. 하지만 정작 왜 올랐는지를 파악하려면 MTS를 나가 포털 뉴스와 텔레그램, 종목 커뮤니티를 다시 찾아봐야 합니다. DAY-JA-VIEW는 강해지는 테마를 탐지하는 순간 관련 뉴스와 상승 소재, 주도주와 확산 정도를 같은 화면에 연결합니다. 사용자는 다른 정보 채널을 돌아다닐 필요 없이 무엇이 움직이는지부터 왜 움직이는지까지 바로 확인할 수 있습니다. 소재를 찾는 시간을 줄이고 매매 판단에 더 많은 시간을 쓸 수 있습니다.");
}

// 05. Feature 2
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "FEATURE 02 · STUDY", "과거 반응이 오늘 소재의 ‘급’을 보여줍니다", 5);
  p.push(text(92, 128, 1010, 34, "비슷한 말이 아니라, 비슷한 사건 구조와 실제 가격 결과를 비교합니다.", 22, C.black, 400));

  p.push(rect(78, 210, 248, 280, C.orange, 22));
  p.push(text(104, 232, 196, 28, "TODAY EVENT", 14, C.white, 800, "center", "middle", { letterSpacing: 1.2 }));
  p.push(text(104, 292, 196, 80, ["정부", "로봇산업 지원 정책"], 23, C.white, 800, "center", "middle", { lineHeight: 34 }));
  p.push(line(118, 396, 286, 396, C.white, 2));
  p.push(text(104, 414, 196, 52, ["정책 · 정부", "기대 단계"], 16, C.white, 400, "center", "middle", { lineHeight: 25 }));

  p.push(arrow(344, 318, 60, 46, C.brown));
  p.push(text(326, 372, 96, 24, "구조 비교", 13, C.brown, 700, "center"));

  p.push(rect(430, 194, 430, 314, C.gray, 22));
  p.push(text(462, 216, 366, 32, "SIMILAR PAST EVENTS", 14, C.orange, 800, "center", "middle", { letterSpacing: 1.1 }));
  const evs = [
    ["정책 지원 기대", "유사도 0.91", "+7.4%", "+12.8%", "+8.6%"],
    ["산업 육성 발표", "유사도 0.87", "+4.1%", "+6.2%", "+1.9%"],
    ["예산 확대 확정", "유사도 0.82", "+6.8%", "+3.7%", "−2.1%"],
  ];
  evs.forEach((e, i) => {
    const y = 266 + i * 72;
    p.push(rect(458, y, 374, 58, C.white, 14));
    p.push(text(476, y, 144, 58, e[0], 16, C.brown, 700));
    p.push(text(616, y, 96, 58, e[1], 12, C.black, 400, "center"));
    p.push(text(700, y, 40, 58, e[2], 12, C.orange, 800, "center"));
    p.push(text(744, y, 40, 58, e[3], 12, C.brown, 800, "center"));
    p.push(text(788, y, 40, 58, e[4], 12, C.black, 700, "center"));
  });
  p.push(text(704, 447, 120, 28, "T+1  T+5  T+20", 12, C.brown, 700, "center"));

  p.push(arrow(878, 318, 60, 46, C.brown));
  p.push(rect(966, 210, 236, 320, C.brown, 22));
  p.push(text(990, 232, 188, 34, "투자자가 얻는 기준", 18, C.white, 800, "center"));
  dotBullet(p, 996, 292, ["하루성 소재인가", "며칠 이어졌는가"], { w: 174, h: 62, color: C.white, dotColor: C.orange, size: 16, lineHeight: 24 });
  dotBullet(p, 996, 370, ["한 종목인가", "테마 전체인가"], { w: 174, h: 62, color: C.white, dotColor: C.orange, size: 16, lineHeight: 24 });
  dotBullet(p, 996, 448, ["누가 실제", "주도주였는가"], { w: 174, h: 62, color: C.white, dotColor: C.orange, size: 16, lineHeight: 24 });
  p.push(rect(158, 560, 964, 72, C.white, 18, C.orange, 3));
  p.push(text(188, 560, 904, 72, "오늘 소재의 영향력 · 지속성 · 확산 범위를 빠르게 판단", 24, C.orange, 800, "center"));
  footer(p);
  addSlide("데자뷰 분석", p,
    "두 번째는 핵심 기능인 데자뷰 분석입니다. 오늘과 비슷했던 과거 Event를 찾되, 같은 단어만 보지 않고 소재 유형과 사건 주체, 기대인지 공식 발표인지까지 비교합니다. 그리고 당시 주도주의 1일, 5일, 20거래일 뒤 움직임을 연결합니다. 테마주 투자자에게는 오늘 오른 이유뿐 아니라 그 소재가 시장에서 어느 정도의 급인지 판단하는 것이 중요합니다. 유사사례를 보면 과거에는 하루 만에 소멸했는지, 여러 거래일 동안 관심이 이어졌는지, 한 종목만 움직였는지 테마 전체가 반응했는지를 확인할 수 있습니다. 오늘 소재의 영향력과 지속성, 실제 주도주를 판단할 기준을 얻는 것이 두 번째 이점입니다.");
}

// 06. Feature 3
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "FEATURE 03 · ASK", "질문 한 문장으로 과거 리서치를 끝냅니다", 6);
  p.push(rect(84, 160, 1112, 108, C.brown, 20));
  p.push(text(118, 178, 64, 58, "Q", 42, C.orange, 800, "center"));
  p.push(text(194, 174, 962, 72, ["“이 정책 소재는 보통 하루 만에 끝났어,", "며칠 이어졌어?”"], 28, C.white, 700, "left", "middle", { lineHeight: 38 }));

  const steps = [
    ["질문 해석", ["테마", "소재", "기간"]],
    ["관계 탐색", ["과거 Event", "주도주"]],
    ["가격 계산", ["T+1", "T+5", "T+20"]],
    ["근거 답변", ["수치", "원문"]],
  ];
  steps.forEach((s, i) => {
    const x = 90 + i * 292;
    p.push(rect(x, 322, 238, 166, i === 3 ? C.orange : C.gray, 20));
    p.push(text(x + 20, 342, 198, 36, `0${i + 1}  ${s[0]}`, 18, i === 3 ? C.white : C.brown, 800));
    p.push(text(x + 20, 395, 198, 72, s[1], 16, i === 3 ? C.white : C.black, 400, "left", "top", { lineHeight: 24 }));
    if (i < steps.length - 1) p.push(arrow(x + 246, 382, 38, 32, C.brown));
  });
  p.push(rect(146, 548, 988, 80, C.white, 20, C.orange, 3));
  p.push(text(182, 560, 916, 28, "복잡한 메뉴·검색 조건 없이", 17, C.brown, 700, "center"));
  p.push(text(182, 589, 916, 32, "내 매매 가설을 질문하고, 데이터 근거로 빠르게 확인", 23, C.orange, 800, "center"));
  footer(p);
  addSlide("자연어 질문", p,
    "세 번째는 자연어 질문입니다. 같은 테마를 보더라도 투자자마다 궁금한 내용은 다릅니다. 어떤 사람은 비슷한 소재가 과거에 며칠 이어졌는지, 또 어떤 사람은 당시 어떤 종목이 가장 강했는지 알고 싶을 수 있습니다. DAY-JA-VIEW에서는 복잡한 메뉴나 검색 조건 없이 ‘이 정책 소재는 보통 하루 만에 끝났어, 며칠 이어졌어?’처럼 평소 말하듯 질문하면 됩니다. 시스템이 관련 과거 사건과 주도주, 가격 움직임을 찾아 근거와 함께 답합니다. 직접 뉴스와 차트를 하나씩 비교하던 과정을 질문 한 문장으로 줄이고, 자신이 가진 매매 가설을 빠르게 확인할 수 있습니다.");
}

// 07. Architecture
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "ARCHITECTURE", "실시간 탐지와 과거 Event 분석을 한 흐름으로", 7);
  p.push(text(92, 128, 980, 34, "수집 · 분석 · 저장 · 전달의 세 계층으로 구성했습니다.", 21, C.black, 400));
  const nodes = [
    ["DATA SOURCE", ["키움 실시간 시세", "뉴스", "인포스탁 과거 데이터"]],
    ["INGEST", ["수집기", "정규화", "실시간 처리"]],
    ["ANALYSIS", ["Event 구조화", "온톨로지", "유사사례 분석"]],
    ["STORAGE", ["PostgreSQL", "Redis", "가격 데이터"]],
    ["DELIVERY", ["REST", "WebSocket", "Query API"]],
    ["WEB", ["탐지", "데자뷰", "자연어 질문"]],
  ];
  nodes.forEach((n, i) => {
    const x = 66 + i * 202;
    const y = i % 2 === 0 ? 246 : 330;
    p.push(rect(x, y, 166, 178, i === 2 ? C.orange : (i === 5 ? C.brown : C.gray), 22));
    p.push(text(x + 18, y + 18, 130, 30, n[0], 13, i === 2 || i === 5 ? C.white : C.orange, 800, "center", "middle", { letterSpacing: 0.8 }));
    p.push(text(x + 18, y + 66, 130, 88, n[1], 15, i === 2 || i === 5 ? C.white : C.brown, 700, "center", "top", { lineHeight: 25 }));
    if (i < nodes.length - 1) p.push(arrow(x + 172, y + 73, 26, 30, C.brown));
  });
  p.push(line(150, 590, 1130, 590, C.orange, 3));
  p.push(circle(380, 590, 9, C.orange));
  p.push(circle(640, 590, 9, C.orange));
  p.push(circle(900, 590, 9, C.orange));
  p.push(text(222, 612, 840, 32, "현재 시장의 변화와 20년치 과거 사건을 동일한 Event 구조로 연결", 21, C.brown, 800, "center"));
  footer(p);
  addSlide("전체 아키텍처", p,
    "전체 아키텍처는 크게 데이터 수집, 분석, 서비스 제공의 세 계층으로 구성했습니다. 키움의 실시간 시세와 뉴스, 인포스탁의 과거 테마 데이터를 수집합니다. 실시간 처리 계층에서는 가격과 거래량 변화로 현재 강해지는 테마를 계산하고, 분석 계층에서는 상승 소재를 Event로 구조화해 과거 사례와 연결합니다. 사건과 온톨로지 데이터는 PostgreSQL에, 빠르게 바뀌는 장중 상태는 Redis에 저장했습니다. 과거 데이터와 상세 정보는 REST로, 실시간 테마 순위와 트리맵은 WebSocket으로 갱신합니다.");
}

// 08. Technical overview
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "CORE TECHNOLOGY", "흩어진 기록을 비교 가능한 Event 데이터로", 8);
  p.push(text(92, 128, 980, 34, "서비스의 핵심은 ‘많은 데이터’가 아니라 ‘비교할 수 있는 데이터’입니다.", 21, C.black, 400));
  const stages = [
    ["01", "COLLECT", "20년치", "테마 기록 수집"],
    ["02", "STRUCTURE", "문장을", "Event로 분해"],
    ["03", "CONNECT", "개체와 관계를", "온톨로지로 연결"],
    ["04", "ANSWER", "과거 비교와", "질문 응답에 활용"],
  ];
  stages.forEach((s, i) => {
    const x = 74 + i * 300;
    p.push(text(x, 207, 66, 54, s[0], 38, C.orange, 800, "center"));
    p.push(text(x + 76, 220, 170, 28, s[1], 14, C.brown, 800, "left", "middle", { letterSpacing: 1.1 }));
    p.push(rect(x, 286, 250, 230, i === 2 ? C.brown : C.gray, 24));
    p.push(circle(x + 125, 346, 25, i === 2 ? C.orange : C.brown));
    p.push(text(x + 32, 390, 186, 80, [s[2], s[3]], 22, i === 2 ? C.white : C.brown, 800, "center", "middle", { lineHeight: 34 }));
    if (i < stages.length - 1) p.push(arrow(x + 258, 386, 34, 32, C.orange));
  });
  p.push(rect(200, 568, 880, 62, C.orange, 31));
  p.push(text(230, 568, 820, 62, "원문 → 의미 구조 → 관계 → 실제 시장 반응", 23, C.white, 800, "center"));
  footer(p);
  addSlide("핵심 기술 개요", p,
    "지금부터 DAY-JA-VIEW를 구현하는 데 사용한 핵심 기술을 소개해드리겠습니다. 오늘의 테마와 과거 사건을 비교하려면, 먼저 과거에 어떤 테마가 어떤 소재로 움직였고 당시 어떤 종목이 주도했는지를 기록한 데이터가 필요했습니다. 하지만 서비스의 핵심은 단순히 데이터를 많이 모으는 것이 아니라, 서로 비교할 수 있는 Event 구조로 바꾸는 것이었습니다.");
}

// 09. Data collection
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 01 · DATA", "화면으로만 보이던 테마 기록을 분석 데이터로", 9);
  p.push(text(92, 128, 980, 34, "인포스탁의 테마 히스토리·주도주·일일 시황을 직접 수집했습니다.", 21, C.black, 400));

  p.push(rect(76, 190, 550, 382, C.gray, 24));
  p.push(rect(106, 222, 490, 46, C.brown, 12));
  p.push(text(126, 222, 450, 46, "THEME HISTORY / INFOSTOCK", 15, C.white, 800, "left", "middle", { letterSpacing: 0.8 }));
  const rows = [
    ["2026.08.17", "로봇", "정부 정책 기대"],
    ["2026.08.12", "원전", "해외 수주"],
    ["2026.08.08", "방산", "수출 계약"],
    ["2026.08.03", "반도체", "실적 전망"],
    ["2026.07.29", "바이오", "임상 결과"],
  ];
  rows.forEach((r, i) => {
    const y = 286 + i * 48;
    p.push(rect(106, y, 490, 40, C.white, 8));
    p.push(text(120, y, 108, 40, r[0], 13, C.brown, 400));
    p.push(text(236, y, 86, 40, r[1], 14, C.orange, 800));
    p.push(text(324, y, 250, 40, r[2], 14, C.black, 400));
  });
  p.push(text(106, 536, 490, 24, "※ 실제 화면 삽입 시 이 프레임을 이미지로 교체", 12, C.brown, 400, "center"));

  p.push(arrow(650, 348, 74, 54, C.orange));
  p.push(text(650, 408, 74, 28, "COLLECT", 12, C.brown, 800, "center"));

  p.push(rect(748, 190, 454, 382, C.brown, 24));
  p.push(text(782, 220, 386, 38, "직접 개발한 수집기", 24, C.white, 800));
  const collect = [
    ["01", "원문 수집", "화면의 기록을 데이터로"],
    ["02", "출처 보존", "원문·수집 시각·변경 이력"],
    ["03", "정규화", "테마·날짜·종목·상승 이유 분리"],
    ["04", "연결", "주도주와 테마 반응 연결"],
  ];
  collect.forEach((c, i) => {
    const y = 286 + i * 62;
    p.push(circle(796, y + 20, 18, C.orange));
    p.push(text(778, y + 2, 36, 36, c[0], 12, C.white, 800, "center"));
    p.push(text(828, y, 126, 40, c[1], 16, C.white, 800));
    p.push(text(956, y, 212, 40, c[2], 14, C.white, 400));
  });
  p.push(text(210, 610, 860, 32, "수집한 문장을 그대로 쓰지 않고, 분석 가능한 구조로 다시 설계했습니다.", 21, C.orange, 800, "center"));
  footer(p);
  addSlide("데이터 수집", p,
    "여러 데이터 소스를 조사한 결과, 인포스탁에서 국내 테마 정보를 오랜 기간 정리해왔다는 점을 발견했습니다. 테마별 과거 히스토리, 당시 주도 종목, 매일 시장에서 부각된 테마와 상승 이유가 기록돼 있습니다. 하지만 바로 분석할 수 있는 데이터베이스 형태로 제공되는 자료는 아니었습니다. 따라서 수집기를 직접 개발해 필요한 자료를 수집하고, 원문과 수집 시각, 변경 이력을 함께 저장했습니다. 이후 테마, 날짜, 종목, 주도주, 상승 이유를 각각 분리하고 연결해 정규화했습니다.");
}

// 10. Dataset scale
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 01 · DATA", "약 20년치 테마 시장의 기억을 확보했습니다", 10);
  p.push(text(92, 128, 980, 34, "오늘의 소재를 과거와 비교할 수 있는 장기 기준선을 만들었습니다.", 21, C.black, 400));
  p.push(rect(78, 205, 704, 344, C.brown, 26));
  p.push(text(118, 228, 250, 30, "THEME HISTORY", 14, C.orange, 800, "left", "middle", { letterSpacing: 1.2 }));
  p.push(text(112, 280, 610, 116, "39,696", 86, C.white, 800));
  p.push(text(558, 334, 142, 46, "건", 28, C.orange, 800, "right"));
  p.push(line(118, 420, 740, 420, C.orange, 3));
  p.push(text(118, 446, 622, 54, "2005 ───────────── 2026", 23, C.white, 700, "center"));
  p.push(text(118, 500, 622, 28, "테마별 과거 원인과 주도 종목", 16, C.white, 400, "center"));

  p.push(rect(820, 205, 382, 162, C.gray, 22));
  p.push(text(850, 226, 322, 28, "DAILY THEME MARKET", 13, C.brown, 800, "center", "middle", { letterSpacing: 0.9 }));
  p.push(text(850, 268, 220, 64, "4,655", 46, C.orange, 800));
  p.push(text(1062, 283, 80, 36, "건", 20, C.brown, 800, "right"));
  p.push(text(850, 330, 322, 24, "2007 ─ 2026", 16, C.brown, 700, "center"));

  p.push(rect(820, 387, 382, 162, C.orange, 22));
  p.push(text(850, 408, 322, 30, "LONGITUDINAL CONTEXT", 13, C.white, 800, "center", "middle", { letterSpacing: 0.9 }));
  p.push(text(850, 452, 322, 54, "약 20년", 36, C.white, 800, "center"));
  p.push(text(850, 510, 322, 24, "같은 소재의 반복과 반응을 비교", 15, C.white, 700, "center"));
  p.push(text(160, 597, 960, 38, "하지만 데이터를 많이 모았다고 바로 ‘데자뷰 분석’이 되는 것은 아닙니다.", 22, C.brown, 800, "center"));
  footer(p);
  addSlide("수집 데이터 규모", p,
    "그 결과, 2005년부터 2026년까지 약 20년치의 테마 히스토리 3만 9,696건을 확보했습니다. 매일 시장에서 부각된 테마와 종목을 정리한 일일 테마 시황도 2007년부터 4,655건을 확보했습니다. 하지만 이렇게 데이터를 많이 모았다고 바로 데자뷰 분석이 가능한 것은 아닙니다.");
}

// 11. Raw text problem
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 02 · PROBLEM", "검색은 가능해도, 사건 비교와 집계는 틀릴 수 있습니다", 11);
  p.push(rect(78, 154, 1124, 118, C.brown, 22));
  p.push(text(108, 170, 1064, 86, ["“정부의 로봇 산업 지원 기대감에 로봇 테마 상승,", "A사·B사 강세”"], 29, C.white, 700, "center", "middle", { lineHeight: 39 }));

  const parsed = [
    ["정부", "Actor", "사건 주체"],
    ["산업 지원", "Catalyst", "상승 소재"],
    ["기대감", "Stage", "진행 단계"],
    ["A사 · B사", "Leader", "주도 종목"],
  ];
  parsed.forEach((d, i) => {
    const x = 78 + i * 286;
    p.push(line(x + 124, 272, x + 124, 317, C.orange, 3));
    p.push(circle(x + 124, 316, 8, C.orange));
    p.push(rect(x, 340, 248, 156, i === 1 ? C.orange : C.gray, 20));
    p.push(text(x + 20, 358, 208, 38, d[0], 21, i === 1 ? C.white : C.brown, 800, "center"));
    p.push(text(x + 20, 402, 208, 28, d[1], 14, i === 1 ? C.white : C.orange, 800, "center", "middle", { letterSpacing: 0.8 }));
    p.push(text(x + 20, 442, 208, 28, d[2], 16, i === 1 ? C.white : C.black, 400, "center"));
  });
  p.push(rect(156, 548, 968, 82, C.white, 20, C.orange, 3));
  p.push(text(184, 556, 912, 32, "문장 하나에 주체 · 소재 · 단계 · 종목 역할이 섞여 있습니다.", 21, C.brown, 700, "center"));
  p.push(text(184, 591, 912, 28, "RAG가 문장을 찾아도 관계를 구분하지 못하면 정확한 통계를 만들 수 없습니다.", 18, C.orange, 800, "center"));
  footer(p);
  addSlide("원문 데이터의 문제", p,
    "여기서 이런 의문이 생길 수 있습니다. 이미 정보가 잘 정리돼 있는데, 그대로 데이터베이스에 넣고 검색하면 되는 것 아닌가? 자연어 질문은 RAG를 붙이면 되는 것 아닌가? 저희도 처음엔 그렇게 생각했습니다. 그런데 사건을 정확히 비교하거나 집계할 때 문제가 생깁니다. 사람은 이 문장에서 정부가 행동 주체이고, 정책 지원이 상승 소재이며, 아직 기대 단계라는 것을 이해합니다. A사와 B사는 정책 발표 주체가 아니라 당시 상승을 주도한 종목입니다. 하지만 일반적인 데이터베이스에는 이 모든 정보가 하나의 문장으로 저장됩니다. 그래서 온톨로지를 구축했습니다.");
}

// 12. Ontology definition and terms
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 03 · ONTOLOGY", "온톨로지는 문장을 관계로 바꾸는 설계도입니다", 12);
  p.push(text(92, 126, 1040, 62, ["사람이 이해하는 개념과 관계를", "컴퓨터도 같은 기준으로 처리하도록 정의한 지식 체계"], 23, C.black, 400, "left", "top", { lineHeight: 34 }));

  p.push(rect(76, 220, 1128, 350, C.gray, 28));
  const nodes = [
    [184, 345, 92, C.orange, "Catalyst", ["정책·발표"]],
    [430, 345, 92, C.brown, "ThemeReaction", ["어떤 테마가", "어떻게 반응"]],
    [686, 345, 76, C.white, "Theme", ["로봇·원전", "방산"]],
    [930, 280, 62, C.white, "Company", ["사건 주체", "주도 종목"]],
    [930, 430, 62, C.white, "Outcome", ["T+1·5·20", "가격 결과"]],
    [184, 520, 48, C.white, "Evidence", []],
  ];
  p.push(arrow(286, 322, 58, 46, C.orange));
  p.push(arrow(536, 322, 58, 46, C.orange));
  p.push(line(760, 345, 860, 292, C.brown, 3));
  p.push(line(760, 365, 860, 440, C.brown, 3));
  p.push(line(184, 437, 184, 472, C.brown, 3));
  nodes.forEach((n) => {
    const [cx, cy, r, fill, label, desc] = n;
    p.push(circle(cx, cy, r, fill, fill === C.white ? C.brown : "none", fill === C.white ? 3 : 0));
    p.push(text(cx - r + 8, cy - 20, r * 2 - 16, 40, label, label === "ThemeReaction" ? 15 : 17, fill === C.brown || fill === C.orange ? C.white : C.brown, 800, "center"));
    p.push(text(cx - r - 24, cy + r + 8, r * 2 + 48, 52, desc, 13, C.brown, 400, "center", "top", { lineHeight: 20 }));
  });
  p.push(rect(80, 596, 1120, 54, C.brown, 27));
  p.push(text(110, 596, 1060, 54, "Event = 특정 날짜의 Catalyst + ThemeReaction + 관련 Company + Outcome + Evidence", 18, C.white, 700, "center"));
  footer(p);
  addSlide("온톨로지", p,
    "온톨로지는 사람이 이해하는 개념과 관계를 컴퓨터도 일관된 기준으로 처리할 수 있도록 정의한 지식 체계입니다. 쉽게 말하면 문장 안의 정보에 이름표를 붙이고 서로의 관계를 연결한 지식 지도입니다. DAY-JA-VIEW에서는 사건의 원인인 Catalyst, 그 사건으로 어떤 테마가 반응했는지를 나타내는 ThemeReaction, 테마와 회사, 가격 결과인 Outcome, 원문 근거인 Evidence를 연결합니다. 사용자가 보는 Event는 특정 날짜에 이 정보들이 함께 묶인 분석 단위입니다.");
}

// 13. Ontology implementation
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 03 · IMPLEMENTATION", "3만 9,696개 기록을 약 2만 개 사건으로 통합했습니다", 13);
  p.push(text(92, 128, 1010, 34, "원문을 분해하고, 역할을 추출하고, 같은 현실 사건을 하나로 합쳤습니다.", 21, C.black, 400));
  const flow = [
    ["원문", ["테마 원인", "기록"]],
    ["사건 분리", ["한 문장 속", "복수 사건 분리"]],
    ["의미 추출", ["유형·단계", "회사 역할"]],
    ["동일 사건 통합", ["중복 표현", "하나로 병합"]],
    ["관계 연결", ["Catalyst", "Reaction·Evidence"]],
  ];
  flow.forEach((f, i) => {
    const x = 68 + i * 244;
    p.push(rect(x, 210, 190, 152, i === 4 ? C.brown : C.gray, 20));
    p.push(text(x + 18, 230, 154, 36, `0${i + 1}  ${f[0]}`, 16, i === 4 ? C.white : C.orange, 800, "center"));
    p.push(text(x + 20, 286, 150, 58, f[1], 15, i === 4 ? C.white : C.brown, 700, "center", "middle", { lineHeight: 24 }));
    if (i < flow.length - 1) p.push(arrow(x + 196, 270, 38, 32, C.orange));
  });
  p.push(rect(68, 398, 1144, 68, C.white, 18, C.brown, 2));
  p.push(text(94, 398, 1092, 68, "PostgreSQL의 개체 테이블 + 관계 테이블로 지식 그래프 구현", 22, C.brown, 800, "center"));

  const metrics = [
    ["86.8%", "사건 원인 분류"],
    ["99.6%", "상승·하락 방향"],
    ["90.0%", "기대·확정 단계"],
  ];
  metrics.forEach((m, i) => {
    const x = 154 + i * 340;
    p.push(rect(x, 506, 294, 112, i === 1 ? C.orange : C.brown, 20));
    p.push(text(x + 20, 516, 254, 54, m[0], 38, C.white, 800, "center"));
    p.push(text(x + 20, 570, 254, 34, m[1], 16, C.white, 700, "center"));
  });
  footer(p);
  addSlide("온톨로지 구현", p,
    "저희는 2005년부터 2026년까지의 테마 원인 기록 3만 9,696건을 분석하고, 그중 5,000건을 직접 검토했습니다. 정책, 수주, 실적 등 사건 원인을 46가지로 분류하고, 상승인지 하락인지, 기대 단계인지 실제 확정인지 구분했습니다. 회사 이름이 달라도 같은 회사로 인식하고, 사건을 일으킨 회사인지 단순 관련 종목인지도 나눴습니다. 여러 테마에 반복 기록된 같은 사건을 하나로 합쳐 약 2만 개의 사건과 근거를 연결했습니다. 사용하지 않은 5,000개 기록으로 시험한 결과 원인 분류 86.8%, 방향 99.6%, 기대·확정 구분 90%를 기록했습니다. 가격 데이터 결합과 통계 계산이 많은 특성을 고려해 PostgreSQL의 개체 테이블과 관계 테이블로 지식 그래프를 구현했습니다.");
}

// 14. RAGAS validation
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 04 · VALIDATION", "온톨로지 기반 검색이 세 지표에서 가장 높았습니다", 14);
  p.push(text(92, 128, 1000, 34, "동일한 80개 질문 · 4개 검색 방식 · Ragas 평가", 21, C.black, 400));

  p.push(rect(68, 190, 278, 408, C.brown, 22));
  p.push(text(94, 214, 226, 30, "EVALUATION SETUP", 13, C.orange, 800, "center", "middle", { letterSpacing: 1.0 }));
  p.push(text(94, 268, 226, 70, "80", 58, C.white, 800, "center"));
  p.push(text(94, 328, 226, 28, "동일 평가 질문", 16, C.white, 700, "center"));
  const methods = ["Keyword RAG", "Vector RAG", "Hybrid RAG", "Ontology Search"];
  methods.forEach((m, i) => {
    p.push(circle(104, 397 + i * 42, 6, C.orange));
    p.push(text(122, 382 + i * 42, 190, 34, m, 15, C.white, i === 3 ? 800 : 400));
  });

  const tx = 374, ty = 198;
  const colW = [270, 138, 138, 138, 160];
  const headers = ["방식", "Precision", "Recall", "Faithfulness", "Factual"];
  let cx = tx;
  headers.forEach((h, i) => {
    p.push(rect(cx, ty, colW[i], 62, i === 0 ? C.gray : C.orange, 0));
    p.push(text(cx + 8, ty, colW[i] - 16, 62, h, 15, i === 0 ? C.brown : C.white, 800, i === 0 ? "left" : "center"));
    cx += colW[i];
  });
  const data = [
    ["Keyword RAG", "0.54", "0.63", "0.66", "0.60"],
    ["Vector RAG", "0.65", "0.74", "0.71", "0.67"],
    ["Hybrid RAG", "0.74", "0.84", "0.79", "0.76"],
    ["온톨로지 기반 구조화 검색", "0.85", "0.82", "0.88", "0.84"],
  ];
  data.forEach((row, r) => {
    let x = tx;
    const y = ty + 62 + r * 70;
    row.forEach((v, i) => {
      const highlight = r === 3 && i !== 2 || r === 2 && i === 2;
      const fill = r === 3 ? (i === 0 ? C.brown : C.white) : (r % 2 === 0 ? C.white : C.gray);
      p.push(rect(x, y, colW[i], 70, fill, 0, C.gray, 1));
      p.push(text(x + 10, y, colW[i] - 20, 70, v, i === 0 ? (r === 3 ? 15 : 16) : 20, r === 3 && i === 0 ? C.white : (highlight ? C.orange : C.brown), highlight ? 800 : (i === 0 ? 700 : 400), i === 0 ? "left" : "center"));
      x += colW[i];
    });
  });
  p.push(rect(374, 565, 838, 70, C.gray, 16));
  p.push(text(394, 573, 798, 24, "Precision · 검색 근거의 정확도    Recall · 필요한 근거의 누락 여부", 14, C.brown, 700, "center"));
  p.push(text(394, 603, 798, 24, "Faithfulness · 근거 충실도    Factual · 사건·수치의 사실 일치도", 14, C.brown, 700, "center"));
  footer(p);
  addSlide("Ragas 성능 검증", p,
    "온톨로지 구조가 실제 답변 품질 향상으로 이어졌는지 검증했습니다. 동일한 평가 질문 80개를 대상으로 Keyword RAG, Vector RAG, Hybrid RAG, 온톨로지 기반 구조화 검색을 비교했습니다. 평가는 Ragas를 사용했습니다. Context Precision은 가져온 자료 중 실제 필요한 근거의 비율, Context Recall은 필요한 근거를 빠짐없이 찾았는지, Faithfulness는 답변이 검색 근거에 충실한지, Factual Correctness는 사건과 수치가 정답과 일치하는지를 봅니다. 측정 결과 저희 방식은 Precision 0.85, Faithfulness 0.88, Factual Correctness 0.84로 세 지표에서 가장 높았습니다. Recall은 Hybrid RAG가 0.84로 조금 높았지만, 저희 방식은 불필요한 문서를 줄이고 더 정확한 근거로 답했습니다.");
}

// 15. Similar-event analysis
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 05 · SIMILARITY", "문장 유사도에 사건의 구조를 더했습니다", 15);
  p.push(text(92, 128, 1020, 34, "소재 유형 · 주체 · 행동 · 진행 단계가 함께 닮은 과거 Event를 찾습니다.", 21, C.black, 400));
  const stages = [
    ["오늘 Event", ["정책", "정부", "기대"]],
    ["과거 후보", ["현재 이전", "사건만"]],
    ["복합 유사도", ["텍스트", "+ 관계"]],
    ["중복 제거", ["동일 사건", "1회 집계"]],
    ["결과 연결", ["T+1", "T+5", "T+20"]],
  ];
  stages.forEach((s, i) => {
    const x = 68 + i * 244;
    const fill = i === 2 ? C.orange : (i === 4 ? C.brown : C.gray);
    const tc = i === 2 || i === 4 ? C.white : C.brown;
    p.push(rect(x, 220, 190, 190, fill, 22));
    p.push(text(x + 18, 240, 154, 36, `0${i + 1}`, 16, i === 2 || i === 4 ? C.white : C.orange, 800, "center"));
    p.push(text(x + 18, 286, 154, 38, s[0], 20, tc, 800, "center"));
    p.push(text(x + 20, 340, 150, 54, s[1], 15, tc, 400, "center", "middle", { lineHeight: 23 }));
    if (i < stages.length - 1) p.push(arrow(x + 196, 299, 38, 32, C.brown));
  });
  p.push(rect(88, 462, 1104, 126, C.white, 22, C.orange, 3));
  p.push(text(116, 480, 330, 34, "표현은 달라도", 18, C.brown, 700));
  p.push(text(116, 516, 330, 42, "실제 사건 구조가 같으면 찾습니다.", 23, C.orange, 800));
  p.push(line(478, 484, 478, 564, C.gray, 3));
  pill(p, 520, 490, 144, "소재 유형", C.brown, C.white, 15);
  pill(p, 674, 490, 126, "사건 주체", C.orange, C.white, 15);
  pill(p, 810, 490, 112, "행동", C.brown, C.white, 15);
  pill(p, 932, 490, 148, "진행 단계", C.orange, C.white, 15);
  p.push(text(520, 542, 560, 28, "문장 임베딩 + 온톨로지 관계 비교", 17, C.brown, 700, "center"));
  p.push(text(196, 613, 888, 34, "사례 선택이 끝난 뒤에만 가격 결과를 연결해 비교 기준을 완성합니다.", 20, C.brown, 800, "center"));
  footer(p);
  addSlide("유사사례 분석", p,
    "구조화한 온톨로지는 데자뷰 유사사례 분석에 사용됩니다. 오늘의 상승 소재가 확인되면 같은 Event 구조로 변환하고, 현재보다 이전에 발생한 사건만 후보로 선택합니다. 문장이 얼마나 비슷한지만 보지 않고 소재 유형, 사건 주체, 행동, 기대인지 공식 발표인지 같은 진행 단계까지 비교합니다. 표현은 달라도 실제 사건 구조가 비슷한 사례를 찾을 수 있습니다. 같은 사건이 반복 기록된 경우를 제거하고 관련성이 높은 사례를 선택한 뒤, 당시 주도주의 1일, 5일, 20거래일 뒤 움직임을 연결합니다.");
}

// 16. Natural language pipeline
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TECH 06 · NATURAL LANGUAGE", "질문을 구조화된 조회와 계산으로 바꿉니다", 16);
  p.push(rect(74, 152, 1132, 86, C.brown, 20));
  p.push(text(102, 166, 1076, 58, "“과거 로봇산업 육성 정책 때 어떤 테마가 반응했고, 주도주는 5거래일 뒤 어떻게 움직였어?”", 21, C.white, 700, "center"));

  p.push(text(82, 276, 180, 28, "QUERY PLAN", 14, C.orange, 800, "center", "middle", { letterSpacing: 1.0 }));
  const slots = [["Theme", "로봇"], ["Catalyst", "정책"], ["Time", "과거"], ["Outcome", "T+5"]];
  slots.forEach((s, i) => {
    const x = 76 + i * 192;
    p.push(rect(x, 322, 166, 94, i === 1 ? C.orange : C.gray, 18));
    p.push(text(x + 14, 336, 138, 24, s[0], 12, i === 1 ? C.white : C.orange, 800, "center"));
    p.push(text(x + 14, 368, 138, 34, s[1], 20, i === 1 ? C.white : C.brown, 800, "center"));
    if (i < slots.length - 1) p.push(line(x + 166, 369, x + 192, 369, C.brown, 2));
  });
  p.push(arrow(842, 346, 58, 44, C.orange));
  p.push(rect(926, 288, 280, 166, C.brown, 20));
  p.push(text(950, 306, 232, 34, "POSTGRESQL", 13, C.orange, 800, "center", "middle", { letterSpacing: 1.0 }));
  p.push(text(950, 350, 232, 74, ["사건 → 테마 → 회사", "관계 조회 + 가격 계산"], 18, C.white, 700, "center", "middle", { lineHeight: 30 }));

  p.push(rect(74, 488, 1132, 124, C.gray, 22));
  p.push(text(100, 508, 118, 80, "A", 48, C.orange, 800, "center"));
  p.push(line(226, 510, 226, 588, C.white, 3));
  p.push(text(260, 506, 912, 38, "구조화된 결과 + 계산된 수치 + 원문 근거", 18, C.brown, 800));
  p.push(text(260, 548, 912, 42, "AI가 기억에 의존하지 않고, 실제 데이터에서 답을 계산합니다.", 23, C.orange, 800));
  footer(p);
  addSlide("자연어 질문 처리", p,
    "자연어 질문도 같은 온톨로지 구조를 사용합니다. 사용자가 ‘과거 로봇산업 육성 정책이 나왔을 때 어떤 테마가 반응했고, 당시 주도주는 5거래일 뒤 어떻게 움직였어?’라고 질문했다고 가정하겠습니다. 시스템은 로봇이라는 테마, 정책이라는 소재, 과거라는 기간, 5거래일 뒤라는 결과 조건을 추출해 QueryPlan으로 변환합니다. 그다음 PostgreSQL에 저장된 사건과 테마, 회사의 관계를 따라 조건에 맞는 사례를 찾고 가격 데이터에서 결과를 계산합니다. 마지막으로 계산된 수치와 근거 사건을 사용자가 이해하기 쉬운 문장으로 제공합니다.");
}

// 17. Knowledge graph trace video
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  header(p, "TRACE VIDEO", "질문 하나가 답변이 되기까지의 실제 경로", 17);
  p.push(text(92, 128, 1030, 34, "PostgreSQL에 저장된 온톨로지 관계와 질의 처리 경로를 시각화합니다.", 21, C.black, 400));
  p.push(rect(68, 188, 778, 420, C.brown, 24));
  p.push(circle(434, 390, 64, C.orange));
  p.push({ type: "triangle", points: [[420, 354], [420, 426], [472, 390]], fill: C.white });
  const graphNodes = [[190, 280, 9], [262, 450, 13], [370, 250, 7], [520, 246, 12], [644, 338, 8], [696, 482, 14], [530, 530, 7], [190, 520, 6]];
  graphNodes.forEach((n, i) => {
    p.push(line(434, 390, n[0], n[1], i % 2 ? C.orange : C.gray, i % 2 ? 2 : 1));
    p.push(circle(n[0], n[1], n[2], i % 3 === 0 ? C.orange : C.white, C.orange, 2));
  });
  p.push(text(112, 548, 690, 32, "질문 입력에 따라 탐색되는 노드와 경로가 달라집니다.", 17, C.white, 700, "center"));

  p.push(rect(876, 188, 336, 420, C.gray, 24));
  p.push(text(906, 212, 276, 34, "TRACE ORDER", 13, C.orange, 800, "left", "middle", { letterSpacing: 1.0 }));
  const trace = ["질문 조건 해석", "Catalyst 탐색", "반응 Theme 연결", "당시 주도주 확인", "가격 결과 계산", "원문 근거 검증"];
  trace.forEach((t, i) => {
    const y = 272 + i * 49;
    p.push(circle(922, y + 14, 13, i === 5 ? C.brown : C.orange));
    p.push(text(909, y + 1, 26, 26, String(i + 1), 10, C.white, 800, "center"));
    p.push(text(950, y, 218, 28, t, 16, C.brown, i === 5 ? 800 : 400));
    if (i < trace.length - 1) p.push(line(922, y + 28, 922, y + 48, C.orange, 2));
  });
  p.push(text(112, 625, 1056, 30, "영상 재생 전 질문을 읽고 → 재생 중에는 설명 없이 경로 변화에 집중", 16, C.brown, 700, "center"));
  footer(p);
  addSlide("온톨로지 관계 탐색 영상", p,
    "[영상 시작 전] 그렇다면 지금까지 설명한 기술들이 실제 질문 하나를 처리할 때 어떻게 연결되는지 보겠습니다. 사용자가 ‘과거 로봇산업 육성 정책이 나왔을 때 어떤 테마가 반응했고, 당시 주도주는 5거래일 뒤 어떻게 움직였어?’라고 질문한 상황입니다. 화면의 관계망은 특정 그래프 데이터베이스 화면이 아니라, 저희가 PostgreSQL에 저장한 온톨로지 관계와 실제 질문 처리 경로를 시각화한 것입니다.\n\n[영상 재생 — 재생 중에는 말하지 않음]\n\n[영상 종료 후] 질문이 입력되면 로봇, 정책, 과거 사건, 5거래일 뒤라는 조건을 해석합니다. 이후 관련 Catalyst와 반응 테마, 당시 주도주, 가격 결과와 원문 근거를 순서대로 탐색한 뒤 답변을 생성합니다.");
}

// 18. XAI close
{
  const p = [];
  p.push(rect(0, 0, W, H, C.white));
  p.push(rect(0, 0, 24, H, C.orange));
  p.push(text(72, 66, 220, 28, "EXPLAINABLE AI", 15, C.orange, 800, "left", "middle", { letterSpacing: 1.5 }));
  p.push(text(72, 144, 790, 122, ["무엇을 답했는가보다,", "왜 그 답이 나왔는가까지"], 48, C.brown, 800, "left", "middle", { lineHeight: 62 }));
  p.push(text(72, 296, 730, 70, ["DAY-JA-VIEW는 질문 해석부터 사건 선택,", "가격 결과와 원문 근거까지 처리 경로를 보여줍니다."], 23, C.black, 400, "left", "top", { lineHeight: 34 }));

  const xs = [128, 370, 612, 854, 1096];
  const labs = [["질문", "조건"], ["Catalyst", "사건"], ["Theme", "반응"], ["Outcome", "가격"], ["Evidence", "근거"]];
  p.push(line(128, 486, 1096, 486, C.orange, 4));
  labs.forEach((l, i) => {
    p.push(circle(xs[i], 486, 30, i === 4 ? C.brown : C.orange));
    p.push(text(xs[i] - 46, 446, 92, 28, `0${i + 1}`, 12, C.brown, 800, "center"));
    p.push(text(xs[i] - 92, 532, 184, 58, l, 17, C.brown, 800, "center", "top", { lineHeight: 25 }));
  });
  p.push(rect(194, 620, 892, 58, C.brown, 29));
  p.push(text(224, 620, 832, 58, "오늘의 움직임을 발견하고, 과거의 근거로 이해하다", 22, C.white, 800, "center"));
  addSlide("설명 가능한 AI", p,
    "이 화면은 AI의 머릿속이나 숨겨진 사고 과정을 표현한 것이 아니라, 실제 답변에 사용된 데이터 관계와 처리 경로를 보여준 것입니다. 최근 AI 분야에서는 답변의 정확성뿐 아니라 왜 그런 답변이 나왔는지를 설명할 수 있는지가 중요합니다. DAY-JA-VIEW도 같은 원칙을 적용했습니다. 사용자의 질문을 어떻게 해석했는지, 어떤 과거 사건을 선택했는지, 어떤 회사와 가격 결과를 사용했는지를 단계별로 확인할 수 있습니다.");
}

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function svgText(item) {
  const lines = Array.isArray(item.value) ? item.value : String(item.value).split("\n");
  const lineHeight = item.lineHeight ?? item.size * 1.22;
  const total = lineHeight * lines.length;
  let y = item.y + item.size;
  if (item.vAlign === "middle") y = item.y + (item.h - total) / 2 + item.size * 0.92;
  if (item.vAlign === "bottom") y = item.y + item.h - total + item.size * 0.92;
  const x = item.align === "center" ? item.x + item.w / 2 : item.align === "right" ? item.x + item.w : item.x;
  const anchor = item.align === "center" ? "middle" : item.align === "right" ? "end" : "start";
  const letter = item.letterSpacing ? ` letter-spacing="${item.letterSpacing}"` : "";
  return `<text x="${x}" y="${y}" fill="${item.color}" font-family="NanumSquare, Arial, sans-serif" font-size="${item.size}" font-weight="${item.weight}" text-anchor="${anchor}"${letter} data-editable="text">${lines.map((ln, i) => `<tspan x="${x}" dy="${i === 0 ? 0 : lineHeight}">${esc(ln)}</tspan>`).join("")}</text>`;
}

function svgPart(item) {
  if (item.type === "text") return svgText(item);
  if (item.type === "rect") {
    const fill = item.fill === "none" ? "none" : item.fill;
    const stroke = item.stroke === "none" ? "none" : item.stroke;
    return `<rect x="${item.x}" y="${item.y}" width="${item.w}" height="${item.h}" rx="${item.radius || 0}" fill="${fill}" stroke="${stroke}" stroke-width="${item.strokeWidth || 0}" data-editable="shape"/>`;
  }
  if (item.type === "line") return `<line x1="${item.x1}" y1="${item.y1}" x2="${item.x2}" y2="${item.y2}" stroke="${item.stroke}" stroke-width="${item.strokeWidth}" stroke-linecap="round" data-editable="line"/>`;
  if (item.type === "circle") return `<circle cx="${item.cx}" cy="${item.cy}" r="${item.r}" fill="${item.fill}" stroke="${item.stroke === "none" ? "none" : item.stroke}" stroke-width="${item.strokeWidth || 0}" data-editable="shape"/>`;
  if (item.type === "arrow") {
    const x = item.x, y = item.y, w = item.w, h = item.h, shaft = h * 0.42;
    const pts = [[x, y + (h - shaft) / 2], [x + w * 0.62, y + (h - shaft) / 2], [x + w * 0.62, y], [x + w, y + h / 2], [x + w * 0.62, y + h], [x + w * 0.62, y + (h + shaft) / 2], [x, y + (h + shaft) / 2]];
    return `<polygon points="${pts.map((q) => q.join(",")).join(" ")}" fill="${item.fill}" data-editable="shape"/>`;
  }
  if (item.type === "triangle") return `<polygon points="${item.points.map((q) => q.join(",")).join(" ")}" fill="${item.fill}" data-editable="shape"/>`;
  return "";
}

function renderSvg(slide, index) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-label="${esc(slide.title)}">
  <title>${esc(slide.title)}</title>
  <style>
    @font-face{font-family:'NanumSquare';src:url('assets/NanumSquareR.woff2') format('woff2');font-weight:400;font-style:normal}
    @font-face{font-family:'NanumSquare';src:url('assets/NanumSquareB.woff2') format('woff2');font-weight:700;font-style:normal}
    @font-face{font-family:'NanumSquare';src:url('assets/NanumSquareEB.woff2') format('woff2');font-weight:800;font-style:normal}
    text{font-kerning:normal}
  </style>
  <g id="slide-${String(index + 1).padStart(2, "0")}" data-title="${esc(slide.title)}">
    ${slide.parts.map(svgPart).join("\n    ")}
  </g>
</svg>`;
}

function addPptPart(slide, item) {
  if (item.type === "text") {
    const shape = slide.shapes.add({
      geometry: "textbox",
      position: { left: item.x, top: item.y, width: item.w, height: item.h },
      fill: "none",
      line: { style: "solid", fill: "none", width: 0 },
    });
    shape.text = (Array.isArray(item.value) ? item.value : String(item.value).split("\n")).join("\n");
    shape.text.style = {
      fontSize: item.size,
      bold: item.weight >= 700,
      color: item.color,
      alignment: item.align,
      verticalAlignment: item.vAlign,
      autoFit: "shrinkText",
      wrap: "square",
      typeface: FONT,
      lineSpacing: item.lineHeight ? item.lineHeight / item.size : 1.12,
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
    };
    return;
  }
  if (item.type === "rect") {
    slide.shapes.add({
      geometry: item.radius ? "roundRect" : "rect",
      position: { left: item.x, top: item.y, width: item.w, height: item.h },
      fill: item.fill,
      line: { style: "solid", fill: item.stroke, width: item.strokeWidth || 0 },
      borderRadius: item.radius || 0,
    });
    return;
  }
  if (item.type === "line") {
    const dx = item.x2 - item.x1;
    const dy = item.y2 - item.y1;
    slide.shapes.add({
      geometry: "line",
      position: {
        left: Math.min(item.x1, item.x2),
        top: Math.min(item.y1, item.y2),
        width: Math.abs(dx),
        height: Math.abs(dy),
        horizontalFlip: dx < 0,
        verticalFlip: dy < 0,
      },
      fill: "none",
      line: { style: "solid", fill: item.stroke, width: item.strokeWidth },
    });
    return;
  }
  if (item.type === "circle") {
    slide.shapes.add({
      geometry: "ellipse",
      position: { left: item.cx - item.r, top: item.cy - item.r, width: item.r * 2, height: item.r * 2 },
      fill: item.fill,
      line: { style: "solid", fill: item.stroke, width: item.strokeWidth || 0 },
    });
    return;
  }
  if (item.type === "arrow") {
    slide.shapes.add({
      geometry: "rightArrow",
      position: { left: item.x, top: item.y, width: item.w, height: item.h },
      fill: item.fill,
      line: { style: "solid", fill: item.fill, width: 0 },
    });
    return;
  }
  if (item.type === "triangle") {
    const xs = item.points.map((q) => q[0]);
    const ys = item.points.map((q) => q[1]);
    const minX = Math.min(...xs), minY = Math.min(...ys), maxX = Math.max(...xs), maxY = Math.max(...ys);
    slide.shapes.add({
      geometry: "triangle",
      position: { left: minX, top: minY, width: maxX - minX, height: maxY - minY, rotation: 90 },
      fill: item.fill,
      line: { style: "solid", fill: item.fill, width: 0 },
    });
  }
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

async function main() {
  await fs.mkdir(SVG_DIR, { recursive: true });
  await fs.mkdir(path.join(SVG_DIR, "assets"), { recursive: true });
  await fs.mkdir(PREVIEW_DIR, { recursive: true });

  const presentation = Presentation.create({ slideSize: { width: W, height: H } });
  presentation.theme.colorScheme = {
    name: "DAY-JA-VIEW",
    themeColors: {
      accent1: C.orange, accent2: C.brown, accent3: C.gray, accent4: C.black,
      accent5: C.orange, accent6: C.brown, bg1: C.white, bg2: C.gray,
      tx1: C.black, tx2: C.brown, dk1: C.black, dk2: C.brown,
      lt1: C.white, lt2: C.gray, hlink: C.orange, folHlink: C.brown,
    },
  };

  for (let i = 0; i < slides.length; i += 1) {
    const def = slides[i];
    const slide = presentation.slides.add();
    slide.background.fill = C.white;
    def.parts.forEach((part) => addPptPart(slide, part));
    slide.speakerNotes.textFrame.setText(`${def.notes}\n\n[Sources]\n- ${SOURCE_NOTE}\n- ${FONT_SOURCE}\n[/Sources]`);
    slide.speakerNotes.setVisible(true);

    const stem = `slide-${String(i + 1).padStart(2, "0")}`;
    await fs.writeFile(path.join(SVG_DIR, `${stem}.svg`), renderSvg(def, i), "utf8");
    await writeBlob(path.join(PREVIEW_DIR, `${stem}.png`), await presentation.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(PREVIEW_DIR, `${stem}.layout.json`), await layout.text(), "utf8");
  }

  const montage = await presentation.export({ format: "webp", montage: { columns: 3, slideWidth: 426, gap: 12, padding: 12, background: C.gray }, scale: 1 });
  await writeBlob(path.join(ROOT, "DAY-JA-VIEW_슬라이드_미리보기.webp"), montage);
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(path.join(ROOT, "DAY-JA-VIEW_발표_슬라이드.pptx"));

  const readme = `# DAY-JA-VIEW SVG 슬라이드\n\n- 캔버스: 1280 × 720 (16:9)\n- 배경: #FFFFFF\n- 컬러: #FD9F28 / #EEEEEE / #401600 / #000000 / #FFFFFF\n- 폰트: NanumSquare (assets 폴더의 WOFF2 사용)\n- 파일: slide-01.svg ~ slide-${String(slides.length).padStart(2, "0")}.svg\n- 폰트 출처: ${FONT_SOURCE}\n\n각 SVG는 텍스트와 도형이 개별 객체로 남아 있어 Figma, Illustrator, Inkscape에서 수정할 수 있습니다.\n슬라이드 09의 왼쪽 프레임은 실제 인포스탁 화면으로 교체할 수 있도록 만든 자리입니다.\n슬라이드 03과 17의 재생 영역은 실제 시연 영상 썸네일 또는 영상 프레임으로 교체하세요.\n`;
  await fs.writeFile(path.join(SVG_DIR, "README.md"), readme, "utf8");

  const inspection = await presentation.inspect({ kind: "slide,textbox,shape,notes", maxChars: 12000 });
  await fs.writeFile(path.join(ROOT, "deck-inspection.ndjson"), inspection.ndjson, "utf8");
  console.log(JSON.stringify({ slides: slides.length, root: ROOT }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
