import axe from 'axe-core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository } from '../adapters/fixtureRepository';
import styles from '../styles/global.css?raw';
import tokens from '../styles/tokens.css?raw';

describe('접근성 foundation', () => {
  it('로그인과 핵심 shell에 자동 검사 가능한 접근성 위반이 없다', async () => {
    const login = render(
      <App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/today']} />,
    );
    await screen.findByRole('heading', { name: '오늘 강한 테마를 확인하세요' });
    const loginResult = await axe.run(login.container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(loginResult.violations).toEqual([]);
    login.unmount();

    const shell = render(<App repository={createFixtureRepository()} initialEntries={['/today']} />);
    await screen.findByRole('heading', { name: '오늘 많이 오른 테마예요' });
    const shellResult = await axe.run(shell.container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(shellResult.violations).toEqual([]);
  });

  it('계산 기준 dialog는 Escape로 닫히고 trigger 초점을 복원한다', async () => {
    const user = userEvent.setup();
    render(
      <App
        repository={createFixtureRepository()}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    const trigger = await screen.findByRole('button', { name: /계산 기준/ });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: '계산 기준' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '계산 기준 닫기' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('시안 토큰과 420px 단일 열, focus-visible, reduced-motion 규칙을 CSS 계약으로 고정한다', () => {
    expect(tokens).toMatch(/--djv-color-brand: #7b2ff7/);
    // 로고는 주색을 따라가지 않는다. 마크만 네이비로 남긴다.
    expect(tokens).toMatch(/--djv-color-logo: #1c1c5e/);
    expect(tokens).toMatch(/--djv-app-max-width: 420px/);
    expect(tokens).toMatch(/--djv-touch-size: 48px/);
    expect(styles).toMatch(/:focus-visible/);
    // 420px 단일 열이라 폭으로 갈리는 화면이 없다. breakpoint 자체를 두지 않는다.
    // 예전에는 440px 경계 하나가 머리띠 곡률만 바꿨는데, 그 머리띠가 떠 있는 카드가
    // 되면서 규칙이 사라졌다. 데스크톱 사이드바 전환도 여전히 두지 않는다.
    expect(styles).not.toMatch(/@media[^{]*width/);
    expect(styles).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(styles).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
    expect(styles).toMatch(/env\(safe-area-inset-bottom\)/);
    // 토큰은 tokens.css 한 곳에서만 정의한다.
    expect(styles).not.toMatch(/--color-accent/);
  });
});
