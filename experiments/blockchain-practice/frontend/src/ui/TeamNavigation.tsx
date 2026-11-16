export function TeamNavigation({ classroom = false, hashcash = false }: { classroom?: boolean; hashcash?: boolean }) {
  return (
    <nav className="team-navigation" aria-label="실습 모드">
      <a href="/">기초 실습</a>
      <a href="/?classroom=join" aria-current={classroom ? "page" : undefined}>공동 실습</a>
      <a href="/hashcash" aria-current={hashcash ? "page" : undefined}>넌스 찾기</a>
    </nav>
  );
}
