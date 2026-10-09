import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { useState } from 'react';
import { describe, expect, test, vi } from 'vitest';
import gatewayFixture from '../../test/fixtures/publish-gateway.json';
import { Outline } from './Outline';
import { parseDefinition } from './model/definition';
import { moveGroup, moveQuestion } from './model/operations';

type SortableOutlineProps = ComponentProps<typeof Outline> & {
  onMoveGroup(groupUuid: string, targetIndex: number): void;
};

function OutlineHarness({ disabled = false }: { disabled?: boolean }) {
  const [definition, setDefinition] = useState(() =>
    parseDefinition(structuredClone(gatewayFixture)),
  );
  const props: SortableOutlineProps = {
    definition,
    disabled,
    onMoveGroup: (groupUuid, targetIndex) => {
      setDefinition((current) => moveGroup(current, groupUuid, targetIndex));
    },
    onMoveQuestion: (questionUuid, groupUuid, targetIndex) => {
      setDefinition((current) =>
        moveQuestion(current, questionUuid, groupUuid, targetIndex),
      );
    },
    onSelect: vi.fn(),
    selectedQuestionUuid: null,
  };

  return <Outline {...props} />;
}

function getGroup(title: string) {
  const section = screen.getByRole('heading', { name: title }).closest('section');
  if (!section) throw new Error(`找不到题组 ${title}`);
  return section;
}

function questionCodes(title: string) {
  return within(getGroup(title))
    .getAllByRole('listitem')
    .map((item) => {
      const question = within(item).getByRole('button', {
        name: /^(QSINGLE|QTEXT|QNOTE|QMULTI|QDUAL)\b/,
      });
      return question.getAttribute('aria-label')?.split(' ')[0];
    });
}

function mockSortableRects() {
  return vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockImplementation(function mockSortableRect(this: HTMLElement) {
      const group = this.matches('.editor-outline-group')
        ? this
        : this.closest('.editor-outline-group');
      const groups = [...document.querySelectorAll('.editor-outline-group')];
      const groupIndex = group ? groups.indexOf(group) : 0;
      const questionIndex = this.matches('li') && group
        ? [...group.querySelectorAll<HTMLLIElement>('li')].indexOf(this as HTMLLIElement)
        : -1;
      const top = groupIndex * 400 + (questionIndex >= 0 ? 60 + questionIndex * 60 : 0);
      return {
        bottom: top + 48,
        height: 48,
        left: 0,
        right: 320,
        top,
        width: 320,
        x: 0,
        y: top,
        toJSON: () => undefined,
      };
    });
}

async function keyboardMove(handleName: string, direction: 'ArrowUp' | 'ArrowDown') {
  const handle = screen.getByRole('button', { name: handleName });
  handle.focus();
  fireEvent.keyDown(handle, { code: 'Space', key: ' ' });
  await waitFor(() => expect(handle.closest('li')).toHaveAttribute('data-dragging', 'true'));
  fireEvent.keyDown(document, { code: direction, key: direction });
  fireEvent.keyDown(document, { code: 'Space', key: ' ' });
}

async function moveUpWithoutPointer(label: string) {
  const user = userEvent.setup();
  const moveButton = screen.queryByRole('button', {
    name: new RegExp(`^上移.*${label}`),
  });
  if (moveButton) {
    await user.click(moveButton);
    return;
  }

  const handle = screen.getByRole('button', {
    name: new RegExp(`拖动.*${label}`),
  });
  handle.focus();
  await user.keyboard('{Space}{ArrowUp}{Space}');
}

describe('Outline sorting', () => {
  test('exposesDragHandlesForEveryQuestionAndGroup', () => {
    render(<OutlineHarness />);

    expect(screen.getAllByRole('button', { name: /拖动题目/ })).toHaveLength(5);
    expect(screen.getAllByRole('button', { name: /拖动题组/ })).toHaveLength(2);
  });

  test('movesAQuestionAcrossGroupsWithoutPointerDragging', async () => {
    render(<OutlineHarness />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '下移 QNOTE' }));

    expect(questionCodes('Basics')).toEqual(['QSINGLE', 'QTEXT']);
    expect(questionCodes('Ratings')).toEqual(['QNOTE', 'QMULTI', 'QDUAL']);
  });

  test('reordersQuestionsAndGroupsWithoutPointerDragging', async () => {
    render(<OutlineHarness />);

    await moveUpWithoutPointer('QTEXT');
    await moveUpWithoutPointer('Ratings');

    expect(questionCodes('Basics')).toEqual(['QTEXT', 'QSINGLE', 'QNOTE']);
    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual([
      'Ratings',
      'Basics',
    ]);
  });

  test('supportsKeyboardDraggingFromTheQuestionHandle', async () => {
    const rectSpy = mockSortableRects();

    try {
      render(<OutlineHarness />);
      await keyboardMove('拖动题目 QTEXT', 'ArrowUp');

      expect(questionCodes('Basics')).toEqual(['QTEXT', 'QSINGLE', 'QNOTE']);
    } finally {
      rectSpy.mockRestore();
    }
  });

  test('movesQuestionsDownByOneWithTheKeyboard', async () => {
    const rectSpy = mockSortableRects();
    try {
      render(<OutlineHarness />);
      await keyboardMove('拖动题目 QSINGLE', 'ArrowDown');
      expect(questionCodes('Basics')).toEqual(['QTEXT', 'QSINGLE', 'QNOTE']);
    } finally {
      rectSpy.mockRestore();
    }
  });

  test('movesTheLastQuestionToTheStartOfTheNextGroupWithTheKeyboard', async () => {
    const rectSpy = mockSortableRects();
    try {
      render(<OutlineHarness />);
      await keyboardMove('拖动题目 QNOTE', 'ArrowDown');
      expect(questionCodes('Basics')).toEqual(['QSINGLE', 'QTEXT']);
      expect(questionCodes('Ratings')).toEqual(['QNOTE', 'QMULTI', 'QDUAL']);
    } finally {
      rectSpy.mockRestore();
    }
  });

  test('disablesEveryReorderEntryPoint', () => {
    render(<OutlineHarness disabled />);

    const handles = [
      ...screen.getAllByRole('button', { name: /拖动题目/ }),
      ...screen.getAllByRole('button', { name: /拖动题组/ }),
    ];
    const explicitActions = screen.queryAllByRole('button', { name: /^(上移|下移)/ });

    expect(handles).toHaveLength(7);
    for (const entry of [...handles, ...explicitActions]) {
      expect(entry).toBeDisabled();
    }
  });

  test('announcesReorderResultsInChinese', async () => {
    render(<OutlineHarness />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '下移 QNOTE' }));

    const announcement = screen
      .getAllByRole('status')
      .find((status) => status.getAttribute('aria-live') === 'polite');
    expect(announcement).toBeDefined();
    expect(announcement).toHaveAttribute('aria-live', 'polite');
    expect(announcement).toHaveTextContent(/题目.*QNOTE.*题组.*Ratings/);
    expect(announcement).toHaveTextContent(/[\u3400-\u9fff]/);
  });
});
