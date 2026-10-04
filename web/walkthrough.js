// Replays the existing local bridge workflow with a fictional student.
// It never approves a draft or records an external send.
export const WALKTHROUGH_MESSAGE = 'I plan to apply to Singapore this year, but I am not sure which part of the application I need help with yet.';

export async function startScriptedWalkthrough(api) {
  const student = await api.createStudent({
    display_name: 'DEMO Student Alex',
    source: { channel: 'OTHER' },
    education: {
      undergraduate_university_raw: 'Demo University',
      major_raw: 'Economics',
      score_raw: '85',
      current_year: 'YEAR_4',
    },
    targets: { countries: ['SG'], universities: [], programs_or_majors: ['Business Analytics'] },
    sales: {
      stage: 'CONSULTING',
      current_objection: 'Unsure which application task needs support',
      decision_maker: 'Student',
      contact_permission: 'ALLOWED',
    },
  });
  await api.recordInbound(student.student_id, { raw_text: WALKTHROUGH_MESSAGE });
  const result = await api.requestDecision(student.student_id);
  if (result.pipeline_outcome?.status !== 'REVIEW_REQUIRED' || !result.drafts?.length) {
    throw new Error('The example did not produce a reviewable draft. Open the student record to inspect the result.');
  }
  return result;
}
