import './SafariPhoneFrame.css';

/**
 * 목업 안쪽을 iframe으로 띄우는 이유:
 * 같은 문서 안에서 div로 감싸면 앱의 `position: fixed`(하단 탭)와 `100dvh`가
 * 목업이 아니라 브라우저 창 전체를 기준으로 잡아서, 정작 확인하려는
 * "주소창·하단 메뉴에 깎인 높이"가 재현되지 않는다. iframe은 자체 뷰포트를 가진다.
 * 안쪽은 프레임 없이 열어야 하므로 `frame` 값을 `phone`이 아닌 것으로 바꿔 넘긴다.
 */
function innerSrc() {
  const url = new URL(window.location.href);
  url.searchParams.set('frame', 'off');
  return url.toString();
}

function SignalIcon() {
  return (
    <svg viewBox="0 0 18 12" aria-hidden="true">
      <path d="M1 11h2V8H1v3Zm4 0h2V6H5v5Zm4 0h2V3H9v8Zm4 0h2V0h-2v11Z" fill="currentColor" />
    </svg>
  );
}

function WifiIcon() {
  return (
    <svg viewBox="0 0 18 14" aria-hidden="true">
      <path d="M1.3 4.7a10.2 10.2 0 0 1 15.4 0M4.2 7.8a6.3 6.3 0 0 1 9.6 0M7.2 10.7a2.4 2.4 0 0 1 3.6 0M9 13.1h.01" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
    </svg>
  );
}

function BatteryIcon() {
  return (
    <svg viewBox="0 0 24 12" aria-hidden="true">
      <rect x="1" y="1.5" width="19" height="9" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M22 4.2v3.6" stroke="currentColor" strokeLinecap="round" strokeWidth="1.3" />
      <rect x="3.2" y="3.5" width="13" height="5" rx="1.2" fill="currentColor" />
    </svg>
  );
}

function BackIcon({ direction = 'left' }: { direction?: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d={direction === 'left' ? 'm14.5 5-7 7 7 7M8 12h11' : 'm9.5 5 7 7-7 7M16 12H5'}
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function ShareIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 15V4m0 0L8 8m4-4 4 4M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function BookmarksIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h7A1.5 1.5 0 0 1 16 4.5V20l-5-3-5 3V4.5Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function TabsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="5" y="5" width="14" height="14" rx="3" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M8 3.5h7a3.5 3.5 0 0 1 3.5 3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

/**
 * Fixture 전용 모바일 목업.
 *
 * `bare`를 켜면 주소창과 하단 메뉴를 뺀다. 홈 화면에 추가해 전체화면으로 연
 * 상태(PWA)와 같은 모습이고, 시연 영상 촬영용이다. 이때 웹 영역은 852에서
 * 상태 표시줄과 홈 인디케이터만 뺀 772px이 된다.
 */
export function SafariPhoneFrame({ bare = false }: { bare?: boolean } = {}) {
  return (
    <div className={`safari-preview${bare ? ' safari-preview--bare' : ''}`}>
      <div className="safari-preview__device">
        <div className="safari-preview__status" aria-hidden="true">
          <span>9:41</span>
          <span className="safari-preview__status-icons">
            <SignalIcon />
            <WifiIcon />
            <BatteryIcon />
          </span>
        </div>
        {bare ? null : (
          <div className="safari-preview__address" aria-label="Safari 주소창">
            <span className="safari-preview__address-lock" aria-hidden="true">⌁</span>
            <span className="safari-preview__address-label">dayjaview.local</span>
            <span className="safari-preview__address-refresh" aria-hidden="true">↻</span>
          </div>
        )}

        <iframe className="safari-preview__viewport" src={innerSrc()} title="DAY-JA-VIEW 미리보기" />

        {bare ? null : (
          <div className="safari-preview__toolbar" aria-label="Safari 하단 메뉴">
            <button type="button" aria-label="뒤로" tabIndex={-1}><BackIcon /></button>
            <button type="button" aria-label="앞으로" tabIndex={-1}><BackIcon direction="right" /></button>
            <button type="button" aria-label="공유" tabIndex={-1}><ShareIcon /></button>
            <button type="button" aria-label="책갈피" tabIndex={-1}><BookmarksIcon /></button>
            <button type="button" aria-label="탭" tabIndex={-1}><TabsIcon /></button>
          </div>
        )}
        <div className="safari-preview__home-indicator" aria-hidden="true" />
      </div>
    </div>
  );
}
