export function progressEstimate(job: { status: string; progress_estimate?: number }): number {
  if (job.status === 'succeeded') return 100;
  return Math.max(0, Math.min(99, Math.round(job.progress_estimate ?? 0)));
}
