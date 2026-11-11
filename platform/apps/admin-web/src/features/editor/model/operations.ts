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
  Pick<EditableQuestion, 'answers' | 'code' | 'condition' | 'mandatory' | 'other' | 'text' | 'themeOptions' | 'slideIds'>
>;

/** 保留服务端保存前后仍对应同一选项的会话身份；重新载入则有意生成新身份。 */
export function preserveCarouselSlideIds(saved: EditableSurveyDefinition, current: EditableSurveyDefinition): EditableSurveyDefinition {
  const byUuid = new Map(current.groups.flatMap((group) => group.questions).map((question) => [question.uuid, question]));
  return { ...saved, groups: saved.groups.map((group) => ({ ...group, questions: group.questions.map((question) => {
    const previous = byUuid.get(question.uuid);
    const oldSlides = previous?.themeOptions?.slides;
    const newSlides = question.themeOptions?.slides;
    if (question.source.theme !== 'mjy-carousel' || previous?.source.theme !== 'mjy-carousel'
      || !Array.isArray(oldSlides) || !Array.isArray(newSlides) || oldSlides.length !== newSlides.length
      || previous.slideIds?.length !== oldSlides.length
      || !oldSlides.every((slide, i) => slide && typeof slide === 'object' && !Array.isArray(slide)
        && newSlides[i] && typeof newSlides[i] === 'object' && !Array.isArray(newSlides[i])
        && (slide as Record<string, unknown>).code === (newSlides[i] as Record<string, unknown>).code)) return question;
    return { ...question, slideIds: [...previous.slideIds] };
  }) })) };
}

export function setCarouselSlideAsset(definition: EditableSurveyDefinition, questionUuid: string, slideId: string, assetId: string): EditableSurveyDefinition {
  const question = definition.groups.flatMap((group) => group.questions).find((item) => item.uuid === questionUuid);
  const index = question?.slideIds?.indexOf(slideId) ?? -1;
  const slides = question?.themeOptions?.slides;
  if (!question?.editable || question.source.theme !== 'mjy-carousel' || index < 0 || !Array.isArray(slides)
    || !slides[index] || typeof slides[index] !== 'object' || Array.isArray(slides[index])) return definition;
  return updateQuestion(definition, questionUuid, { themeOptions: { ...question.themeOptions,
    slides: slides.map((slide, i) => i === index ? { ...slide, assetId } : slide) } });
}

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
  if (update.condition !== undefined) owned.condition = update.condition;
  if (update.text !== undefined) owned.text = update.text;
  if (update.mandatory !== undefined) owned.mandatory = update.mandatory;
  if (update.other !== undefined) owned.other = update.other;
  if (update.answers !== undefined) owned.answers = update.answers.map(copyChoice);
  if (update.themeOptions !== undefined) owned.themeOptions = structuredClone(update.themeOptions);
  if (update.slideIds !== undefined) owned.slideIds = [...update.slideIds];
  return owned;
}

function copyChoice(choice: EditableChoice): EditableChoice {
  return { ...choice, source: structuredClone(choice.source) };
}
