export function ResearchPage() {
  return (
    <div className="page page--research">
      <header className="page-intro">
        <small>데이터 리서치</small>
        <h1>무엇이 궁금하세요?</h1>
        <p>질문하면 보유한 과거 데이터 안에서 답을 찾아요.</p>
      </header>
      {/* 자연어 질의·응답은 요구사항 문서가 아직 없어 구현하지 않는다 (adaptation plan §4.2).
          가짜 질문 입력과 답변을 렌더링하지 않고 화면 자리만 유지한다. */}
      <div className="research-pending">
        <strong>준비 중인 화면이에요</strong>
        <p>
          저장된 과거 사건과 가격 데이터만으로 답하는 리서치를 준비하고 있어요. 준비가 끝나기 전에는 답변을
          만들어 보여드리지 않습니다.
        </p>
      </div>
    </div>
  );
}
