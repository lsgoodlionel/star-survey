import { useRef, useState } from 'react';
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type KeyboardCoordinateGetter,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-react';
import type { EditableGroup, EditableQuestion, EditableSurveyDefinition } from './model/definition';

interface OutlineProps {
  definition: EditableSurveyDefinition;
  selectedQuestionUuid: string | null;
  disabled: boolean;
  onMoveGroup(groupUuid: string, targetIndex: number): void;
  onMoveQuestion(questionUuid: string, groupUuid: string, targetIndex: number): void;
  onSelect(questionUuid: string): void;
}

type SortableData =
  | { kind: 'group'; groupUuid: string }
  | { kind: 'question'; groupUuid: string; questionUuid: string };

export function Outline({
  definition,
  selectedQuestionUuid,
  disabled,
  onMoveGroup,
  onMoveQuestion,
  onSelect,
}: OutlineProps) {
  const [announcement, setAnnouncement] = useState('');
  const keyboardDirection = useRef<-1 | 1 | null>(null);
  const keyboardCoordinates: KeyboardCoordinateGetter = (event, arguments_) => {
    if (event.code === 'ArrowUp') keyboardDirection.current = -1;
    if (event.code === 'ArrowDown') keyboardDirection.current = 1;
    return sortableKeyboardCoordinates(event, arguments_);
  };
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: keyboardCoordinates }),
  );
  const announcements: Announcements = {
    onDragStart: ({ active }) => `已拾取${sortableLabel(definition, active.data.current as SortableData)}`,
    onDragOver: ({ active, over }) => over
      ? `${sortableLabel(definition, active.data.current as SortableData)}当前目标为${sortableLabel(definition, over.data.current as SortableData)}`
      : `${sortableLabel(definition, active.data.current as SortableData)}不在可放置区域`,
    onDragEnd: ({ active, over }) => over
      ? `${sortableLabel(definition, active.data.current as SortableData)}已放置`
      : `${sortableLabel(definition, active.data.current as SortableData)}的移动已取消`,
    onDragCancel: ({ active }) => `${sortableLabel(definition, active.data.current as SortableData)}的移动已取消`,
  };

  function moveGroupWithAnnouncement(groupUuid: string, targetIndex: number) {
    const group = definition.groups.find((candidate) => candidate.uuid === groupUuid);
    if (!group) return;
    onMoveGroup(groupUuid, targetIndex);
    setAnnouncement(`题组 ${group.title} 已移动到第 ${targetIndex + 1} 位`);
  }

  function moveQuestionWithAnnouncement(
    questionUuid: string,
    targetGroupUuid: string,
    targetIndex: number,
  ) {
    const question = findQuestion(definition, questionUuid)?.question;
    const targetGroup = definition.groups.find((group) => group.uuid === targetGroupUuid);
    if (!question || !targetGroup) return;
    onMoveQuestion(questionUuid, targetGroupUuid, targetIndex);
    const source = findQuestion(definition, questionUuid);
    const targetLength = targetGroup.questions.length - (
      source?.groupIndex === definition.groups.indexOf(targetGroup) ? 1 : 0
    );
    const finalIndex = Math.max(0, Math.min(targetIndex, targetLength));
    setAnnouncement(
      `题目 ${question.code} 已移动到题组 ${targetGroup.title} 第 ${finalIndex + 1} 位`,
    );
  }

  function moveQuestionByOffset(questionUuid: string, offset: -1 | 1) {
    const location = findQuestion(definition, questionUuid);
    if (!location) return;
    const { groupIndex, questionIndex } = location;
    const group = definition.groups[groupIndex];
    if (offset === -1 && questionIndex > 0) {
      moveQuestionWithAnnouncement(questionUuid, group.uuid, questionIndex - 1);
      return;
    }
    if (offset === 1 && questionIndex < group.questions.length - 1) {
      moveQuestionWithAnnouncement(questionUuid, group.uuid, questionIndex + 1);
      return;
    }
    const targetGroup = definition.groups[groupIndex + offset];
    if (!targetGroup) return;
    moveQuestionWithAnnouncement(
      questionUuid,
      targetGroup.uuid,
      offset === -1 ? targetGroup.questions.length : 0,
    );
  }

  function handleDragEnd(event: DragEndEvent) {
    if (disabled) return;
    const active = event.active.data.current as SortableData | undefined;
    const direction = event.activatorEvent instanceof KeyboardEvent
      ? keyboardDirection.current
      : null;
    keyboardDirection.current = null;
    if (!active) return;

    if (direction !== null) {
      if (active.kind === 'question') {
        moveQuestionByOffset(active.questionUuid, direction);
      } else {
        const sourceIndex = definition.groups.findIndex(
          (group) => group.uuid === active.groupUuid,
        );
        const targetIndex = sourceIndex + direction;
        if (sourceIndex >= 0 && targetIndex >= 0 && targetIndex < definition.groups.length) {
          moveGroupWithAnnouncement(active.groupUuid, targetIndex);
        }
      }
      return;
    }

    if (!event.over || event.active.id === event.over.id) return;
    const over = event.over.data.current as SortableData | undefined;
    if (!over) return;

    if (active.kind === 'group') {
      const sourceIndex = definition.groups.findIndex((group) => group.uuid === active.groupUuid);
      const targetIndex = definition.groups.findIndex((group) => group.uuid === over.groupUuid);
      if (targetIndex >= 0 && targetIndex !== sourceIndex) {
        moveGroupWithAnnouncement(active.groupUuid, targetIndex);
      }
      return;
    }

    if (active.kind === 'question' && over.kind === 'group') {
      const targetGroup = definition.groups.find((group) => group.uuid === over.groupUuid);
      if (targetGroup) {
        moveQuestionWithAnnouncement(
          active.questionUuid,
          targetGroup.uuid,
          targetGroup.questions.length,
        );
      }
      return;
    }

    if (active.kind === 'question' && over.kind === 'question') {
      const targetGroup = definition.groups.find((group) => group.uuid === over.groupUuid);
      let targetIndex = targetGroup?.questions.findIndex(
        (question) => question.uuid === over.questionUuid,
      );
      if (targetGroup && targetIndex !== undefined && targetIndex >= 0) {
        const translated = event.active.rect.current.translated;
        const droppedAfter = translated
          ? translated.top + translated.height / 2 > event.over.rect.top + event.over.rect.height / 2
          : false;
        if (droppedAfter) targetIndex += 1;
        const source = findQuestion(definition, active.questionUuid);
        const targetGroupIndex = definition.groups.indexOf(targetGroup);
        if (source?.groupIndex === targetGroupIndex && source.questionIndex < targetIndex) {
          targetIndex -= 1;
        }
        moveQuestionWithAnnouncement(active.questionUuid, targetGroup.uuid, targetIndex);
      }
    }
  }

  return (
    <div className="editor-outline">
      <h2>问卷大纲</h2>
      <DndContext
        accessibility={{
          announcements,
          screenReaderInstructions: {
            draggable: '按空格键拾取，使用方向键移动，再按空格键放下；按 Escape 取消。',
          },
        }}
        collisionDetection={closestCenter}
        sensors={sensors}
        onDragEnd={handleDragEnd}
        onDragCancel={() => { keyboardDirection.current = null; }}
      >
        <SortableContext
          items={definition.groups.map((group) => groupId(group.uuid))}
          strategy={verticalListSortingStrategy}
        >
          {definition.groups.map((group, groupIndex) => (
            <SortableGroup
              key={group.uuid}
              group={group}
              groupIndex={groupIndex}
              groupCount={definition.groups.length}
              disabled={disabled}
              selectedQuestionUuid={selectedQuestionUuid}
              isFirstQuestionGroup={groupIndex === 0}
              isLastQuestionGroup={groupIndex === definition.groups.length - 1}
              onMoveGroup={(targetIndex) => moveGroupWithAnnouncement(group.uuid, targetIndex)}
              onMoveQuestion={moveQuestionByOffset}
              onSelect={onSelect}
            />
          ))}
        </SortableContext>
      </DndContext>
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </div>
  );
}

interface SortableGroupProps {
  group: EditableGroup;
  groupIndex: number;
  groupCount: number;
  disabled: boolean;
  selectedQuestionUuid: string | null;
  isFirstQuestionGroup: boolean;
  isLastQuestionGroup: boolean;
  onMoveGroup(targetIndex: number): void;
  onMoveQuestion(questionUuid: string, offset: -1 | 1): void;
  onSelect(questionUuid: string): void;
}

function SortableGroup({
  group,
  groupIndex,
  groupCount,
  disabled,
  selectedQuestionUuid,
  isFirstQuestionGroup,
  isLastQuestionGroup,
  onMoveGroup,
  onMoveQuestion,
  onSelect,
}: SortableGroupProps) {
  const {
    attributes,
    isDragging,
    listeners,
    setActivatorNodeRef,
    setNodeRef,
    transform,
    transition,
  } = useSortable({
    id: groupId(group.uuid),
    data: { kind: 'group', groupUuid: group.uuid } satisfies SortableData,
    disabled,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <section
      className="editor-outline-group"
      ref={setNodeRef}
      style={style}
      data-dragging={isDragging || undefined}
    >
      <div className="editor-outline-group-header">
        <button
          className="editor-drag-handle"
          ref={setActivatorNodeRef}
          type="button"
          aria-label={`拖动题组 ${group.title}`}
          title="拖动题组"
          disabled={disabled}
          {...attributes}
          {...listeners}
        >
          <GripVertical aria-hidden="true" />
        </button>
        <h3>{group.title}</h3>
        <ReorderButtons
          label={group.title}
          kind="题组"
          disabled={disabled}
          disableUp={groupIndex === 0}
          disableDown={groupIndex === groupCount - 1}
          onUp={() => onMoveGroup(groupIndex - 1)}
          onDown={() => onMoveGroup(groupIndex + 1)}
        />
      </div>
      <SortableContext
        items={group.questions.map((question) => questionId(question.uuid))}
        strategy={verticalListSortingStrategy}
      >
        <ul>
          {group.questions.map((question, questionIndex) => (
            <SortableQuestion
              key={question.uuid}
              question={question}
              groupUuid={group.uuid}
              disabled={disabled}
              selected={question.uuid === selectedQuestionUuid}
              disableUp={isFirstQuestionGroup && questionIndex === 0}
              disableDown={isLastQuestionGroup && questionIndex === group.questions.length - 1}
              onMove={(offset) => onMoveQuestion(question.uuid, offset)}
              onSelect={() => onSelect(question.uuid)}
            />
          ))}
        </ul>
      </SortableContext>
    </section>
  );
}

interface SortableQuestionProps {
  question: EditableQuestion;
  groupUuid: string;
  disabled: boolean;
  selected: boolean;
  disableUp: boolean;
  disableDown: boolean;
  onMove(offset: -1 | 1): void;
  onSelect(): void;
}

function SortableQuestion({
  question,
  groupUuid,
  disabled,
  selected,
  disableUp,
  disableDown,
  onMove,
  onSelect,
}: SortableQuestionProps) {
  const {
    attributes,
    isDragging,
    listeners,
    setActivatorNodeRef,
    setNodeRef,
    transform,
    transition,
  } = useSortable({
    id: questionId(question.uuid),
    data: { kind: 'question', groupUuid, questionUuid: question.uuid } satisfies SortableData,
    disabled,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      data-dragging={isDragging || undefined}
    >
      <button
        className="editor-drag-handle"
        ref={setActivatorNodeRef}
        type="button"
        aria-label={`拖动题目 ${question.code}`}
        title="拖动题目"
        disabled={disabled}
        {...attributes}
        {...listeners}
      >
        <GripVertical aria-hidden="true" />
      </button>
      <button
        className="editor-outline-question"
        type="button"
        aria-label={`${question.code} ${question.text}`}
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
      >
        <span>{question.code}</span>
        <small>{question.text}</small>
      </button>
      <ReorderButtons
        label={question.code}
        kind="题目"
        disabled={disabled}
        disableUp={disableUp}
        disableDown={disableDown}
        onUp={() => onMove(-1)}
        onDown={() => onMove(1)}
      />
    </li>
  );
}

interface ReorderButtonsProps {
  label: string;
  kind: '题目' | '题组';
  disabled: boolean;
  disableUp: boolean;
  disableDown: boolean;
  onUp(): void;
  onDown(): void;
}

function ReorderButtons({
  label,
  kind,
  disabled,
  disableUp,
  disableDown,
  onUp,
  onDown,
}: ReorderButtonsProps) {
  return (
    <div className="editor-reorder-actions" aria-label={`${label} 排序`}>
      <button
        type="button"
        title={`上移${kind}`}
        aria-label={`上移 ${label}`}
        disabled={disabled || disableUp}
        onClick={onUp}
      >
        <ChevronUp aria-hidden="true" />
      </button>
      <button
        type="button"
        title={`下移${kind}`}
        aria-label={`下移 ${label}`}
        disabled={disabled || disableDown}
        onClick={onDown}
      >
        <ChevronDown aria-hidden="true" />
      </button>
    </div>
  );
}

function findQuestion(definition: EditableSurveyDefinition, questionUuid: string) {
  for (const [groupIndex, group] of definition.groups.entries()) {
    const questionIndex = group.questions.findIndex((question) => question.uuid === questionUuid);
    if (questionIndex >= 0) {
      return { groupIndex, questionIndex, question: group.questions[questionIndex] };
    }
  }
  return null;
}

function groupId(uuid: string) {
  return `group:${uuid}`;
}

function questionId(uuid: string) {
  return `question:${uuid}`;
}

function sortableLabel(definition: EditableSurveyDefinition, data: SortableData | undefined) {
  if (!data) return '排序项';
  if (data.kind === 'group') {
    const group = definition.groups.find((candidate) => candidate.uuid === data.groupUuid);
    return `题组 ${group?.title ?? ''}`.trim();
  }
  const question = findQuestion(definition, data.questionUuid)?.question;
  return `题目 ${question?.code ?? ''}`.trim();
}
