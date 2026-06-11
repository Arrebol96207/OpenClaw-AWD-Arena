export const submissionReasonLabel = (reason: unknown): string => {
  switch (reason) {
    case 'invalid_flag':
      return '无效 Flag'
    case 'own_flag':
      return '提交了自己的 Flag'
    case 'missing_target_player':
      return '缺少 target_player_id'
    case 'target_mismatch':
      return 'target_player_id 与 Flag 归属不匹配'
    case 'flag_already_claimed_by_attacker':
      return '该选手已提交过这个 Flag'
    case 'success':
      return '成功'
    default:
      return String(reason ?? 'unknown')
  }
}
