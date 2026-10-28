import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ApiClient } from '../../shared/api/http';
import { assetListQueryKey, imageAssets, listAssets, uploadAsset } from '../../shared/api/assets';

interface AssetPickerProps {
  api: ApiClient;
  tenantId: string;
  label: string;
  assetId: string;
  disabled: boolean;
  onChange(assetId: string): void;
}

export function AssetPicker({ api, tenantId, label, assetId, disabled, onChange }: AssetPickerProps) {
  const client = useQueryClient();
  const assets = useQuery({ queryKey: assetListQueryKey(tenantId),
    queryFn: ({ signal }) => listAssets(api, undefined, signal), retry: false });
  const upload = useMutation({ mutationFn: (file: File) => uploadAsset(api, file.name, file),
    onSuccess: async (asset) => { await client.invalidateQueries({ queryKey: assetListQueryKey(tenantId) });
      onChange(asset.id); } });
  const choices = imageAssets(assets.data ?? []);
  return <div className="branding-asset-picker">
    <label>{label} <select aria-label={label} value={assetId} disabled={disabled || upload.isPending}
      onChange={(event) => onChange(event.target.value)}>
      <option value="">不使用图片</option>
      {choices.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
    </select></label>
    {!disabled ? <label>上传{label} <input type="file" accept="image/png,image/jpeg,image/gif,image/webp"
      disabled={upload.isPending} onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file) upload.mutate(file);
      }} /></label> : null}
    {assets.isPending ? <p role="status">正在加载图片资源</p> : null}
    {assets.isError ? <p role="alert">图片资源加载失败</p> : null}
    {upload.isPending ? <p role="status">正在上传图片，完成时间视文件大小而定</p> : null}
    {upload.isError ? <p role="alert">图片上传失败，请重新选择文件重试</p> : null}
  </div>;
}
