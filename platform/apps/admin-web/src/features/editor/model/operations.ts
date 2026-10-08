import type {
  EditableChoice,
  EditableQuestion,
  EditableSurveyDefinition,
} from './definition';

export interface DefinitionValidationIssue {
  code: 'duplicate-question-code' | 'blank-choice';
  message: string;
  questionUuid: string;
}

type QuestionUpdate = Partial<
  Pick<EditableQuestion, 'answers' | 'code' | 'mandatory' | 'other' | 'text'>
>;

export function updateQuestion(
  definition: EditableSurveyDefinition,
  questionUuid: string,
  update: QuestionUpdate,
): EditableSurveyDefinition {
  return {
    ...definition,
    groups: definition.groups.map((group) => ({
      ...group,
      questions: group.questions.map((question) =>
        question.uuid === questionUuid && question.editable
          ? { ...question, ...ownedQuestionUpdate(update) }
          : question,
      ),
    })),
  };
}

export function moveQuestion(
  definition: EditableSurveyDefinition,
  questionUuid: string,
  targetGroupUuid: string,
  targetIndex: number,
): EditableSurveyDefinition {
  const question = definition.groups
    .flatMap((group) => group.questions)
    .find((candidate) => candidate.uuid === questionUuid);
  if (!question || !definition.groups.some((group) => group.uuid === targetGroupUuid)) {
    return definition;
  }

  return {
    ...definition,
    groups: definition.groups.map((group) => {
      const questions = group.questions.filter((candidate) => candidate.uuid !== questionUuid);
      if (group.uuid !== targetGroupUuid) return { ...group, questions };
      const insertionIndex = Math.max(0, Math.min(targetIndex, questions.length));
      return {
        ...group,
        questions: [
          ...questions.slice(0, insertionIndex),
          question,
          ...questions.slice(insertionIndex),
        ],
      };
    }),
  };
}

export function moveGroup(
  definition: EditableSurveyDefinition,
  groupUuid: string,
  targetIndex: number,
): EditableSurveyDefinition {
  const group = definition.groups.find((candidate) => candidate.uuid === groupUuid);
  if (!group) return definition;
  const groups = definition.groups.filter((candidate) => candidate.uuid !== groupUuid);
  const insertionIndex = Math.max(0, Math.min(targetIndex, groups.length));
  groups.splice(insertionIndex, 0, group);
  return { ...definition, groups };
}

export function validateDefinition(
  definition: EditableSurveyDefinition,
): DefinitionValidationIssue[] {
  const issues: DefinitionValidationIssue[] = [];
  const questionCodes = new Set<string>();
  for (const question of definition.groups.flatMap((group) => group.questions)) {
    const normalizedCode = question.code.trim().toLocaleUpperCase('en-US');
    if (questionCodes.has(normalizedCode)) {
      issues.push({
        code: 'duplicate-question-code',
        message: `题目编码 ${question.code} 重复`,
        questionUuid: question.uuid,
      });
    }
    questionCodes.add(normalizedCode);

    if (question.editable && (question.type === 'L' || question.type === 'M')) {
      for (const answer of question.answers ?? []) {
        if (!answer.code.trim() || !answer.text.trim()) {
          issues.push({
            code: 'blank-choice',
            message: `题目 ${question.code} 存在空白选项`,
            questionUuid: question.uuid,
          });
          break;
        }
      }
    }
  }
  return issues;
}

function ownedQuestionUpdate(update: QuestionUpdate): QuestionUpdate {
  const owned: QuestionUpdate = {};
  if (update.code !== undefined) owned.code = update.code;
  if (update.text !== undefined) owned.text = update.text;
  if (update.mandatory !== undefined) owned.mandatory = update.mandatory;
  if (update.other !== undefined) owned.other = update.other;
  if (update.answers !== undefined) owned.answers = update.answers.map(copyChoice);
  return owned;
}

function copyChoice(choice: EditableChoice): EditableChoice {
  return { ...choice, source: structuredClone(choice.source) };
}
