export function TeamNavigation({ classroom = false }: { classroom?: boolean }) {
  return (
    <nav className="team-navigation" aria-label="실습 모드">
      <a href="/">기초 실습</a>
      <a href="/?classroom=join" aria-current={classroom ? "page" : undefined}>공동 실습</a>
    </nav>
  );
}
