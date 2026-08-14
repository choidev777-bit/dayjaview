import axe from 'axe-core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository } from '../adapters/fixtureRepository';
import styles from '../styles/global.css?raw';

describe('접근성 foundation', () => {
  it('로그인과 핵심 shell에 자동 검사 가능한 접근성 위반이 없다', async () => {
    const login = render(
      <App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/today']} />,
    );
    await screen.findByRole('heading', { name: 'DAYJAVIEW' });
    const loginResult = await axe.run(login.container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(loginResult.violations).toEqual([]);
    login.unmount();

    const shell = render(<App repository={createFixtureRepository()} initialEntries={['/today']} />);
    await screen.findByRole('heading', { name: '오늘' });
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

    const trigger = await screen.findByRole('button', { name: '계산 기준 보기' });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: '계산 기준' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '계산 기준 닫기' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('responsive, focus-visible, reduced-motion 규칙을 CSS 계약으로 고정한다', () => {
    expect(styles).toMatch(/:focus-visible/);
    expect(styles).toMatch(/@media \(min-width: 40rem\)/);
    expect(styles).toMatch(/@media \(min-width: 64rem\)/);
    expect(styles).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(styles).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
    expect(styles).toMatch(/env\(safe-area-inset-bottom\)/);
  });
});
