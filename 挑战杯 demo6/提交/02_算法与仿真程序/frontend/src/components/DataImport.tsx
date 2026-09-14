import React, { useState, useCallback } from 'react';
import {
  Upload,
  Card,
  Descriptions,
  Alert,
  Space,
  Typography,
  Tag,
  Button,
} from 'antd';
import {
  CheckCircleOutlined,
  WarningOutlined,
  FileExcelOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { UploadResponse } from '../types';
import { uploadExcel } from '../api';
import { COLORS } from '../theme';

const { Dragger } = Upload;
const { Title, Text } = Typography;

interface Props {
  onUploaded: (res: UploadResponse) => void;
}

const DataImport: React.FC<Props> = ({ onUploaded }) => {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dataFile, setDataFile] = useState<File | null>(null);
  const [speedFile, setSpeedFile] = useState<File | null>(null);

  const doUpload = useCallback(async (dataF: File, speedF: File | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await uploadExcel(dataF, speedF);
      setResult(res);
      onUploaded(res);
    } catch (e: any) {
      const msg = e?.response?.data?.detail ?? e?.message ?? '上传失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [onUploaded]);

  const handleDataFile = useCallback((file: File) => {
    setDataFile(file);
    setResult(null);
    setError(null);
    // 如果附件3还没选，等用户点击"开始上传"
    return false;
  }, []);

  const handleSpeedFile = useCallback((file: File) => {
    setSpeedFile(file);
    return false;
  }, []);

  const handleStartUpload = useCallback(() => {
    if (dataFile) {
      doUpload(dataFile, speedFile);
    }
  }, [dataFile, speedFile, doUpload]);

  const issueKeys = ['未匹配零件数', '零件数不一致钢板数', '小件数不一致钢板数', '大件数不一致钢板数', 'V坡长度不一致钢板数', '打磨长度缺失零件数'];
  const hasIssues = result && issueKeys.some((k) => Number(result.validation[k]) > 0);

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <Title level={4} style={{ marginBottom: 16 }}>
        📂 上传数据文件
      </Title>

      <Alert
        type="info"
        message="需上传两个 Excel 文件：附件2（钢板零件数据，必选）+ 附件3（工艺用时计算表，可选）"
        style={{ marginBottom: 16 }}
        showIcon
      />

      {/* 附件2 */}
      <Card size="small" title="📋 附件2：钢板零件数据（必选）" style={{ marginBottom: 12 }}>
        <Dragger
          accept=".xlsx,.xls"
          maxCount={1}
          showUploadList={true}
          beforeUpload={handleDataFile}
          disabled={loading}
          fileList={dataFile ? [{ uid: 'data', name: dataFile.name, status: 'done' } as any] : []}
          onRemove={() => { setDataFile(null); setResult(null); }}
        >
          <p className="ant-upload-drag-icon">
            <FileExcelOutlined style={{ color: COLORS.primary, fontSize: 32 }} />
          </p>
          <p className="ant-upload-text">点击或拖拽附件2到此</p>
          <p className="ant-upload-hint">需包含「钢板数据」和「零件数据」两个 Sheet</p>
        </Dragger>
      </Card>

      {/* 附件3 */}
      <Card size="small" title="⚡ 附件3：工艺用时计算表（可选，强烈推荐）" style={{ marginBottom: 16 }}>
        <Dragger
          accept=".xlsx,.xls"
          maxCount={1}
          showUploadList={true}
          beforeUpload={handleSpeedFile}
          disabled={loading}
          fileList={speedFile ? [{ uid: 'speed', name: speedFile.name, status: 'done' } as any] : []}
          onRemove={() => setSpeedFile(null)}
        >
          <p className="ant-upload-drag-icon">
            <ThunderboltOutlined style={{ color: COLORS.warning, fontSize: 32 }} />
          </p>
          <p className="ant-upload-text">点击或拖拽附件3到此</p>
          <p className="ant-upload-hint">包含厚度-速度对照表和工序用时公式</p>
        </Dragger>
      </Card>

      {/* 上传按钮 */}
      <Button
        type="primary"
        size="large"
        block
        loading={loading}
        disabled={!dataFile}
        onClick={handleStartUpload}
        icon={<CheckCircleOutlined />}
        style={{ marginBottom: 24 }}
      >
        {loading ? '正在校验…' : '开始上传并校验'}
      </Button>

      {error && (
        <Alert type="error" message={error} showIcon closable style={{ marginBottom: 16 }} />
      )}

      {result && (
        <>
          <Alert
            type={hasIssues ? 'warning' : 'success'}
            message={hasIssues ? '数据校验完成（存在需要注意的项）' : '数据校验通过 ✓'}
            showIcon
            style={{ marginBottom: 16 }}
          />

          <Card title="📊 数据概览" size="small" style={{ marginBottom: 16 }}>
            <Descriptions column={3} size="small">
              <Descriptions.Item label="钢板数">
                <Tag color="blue">{result.summary.钢板数}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="零件数">
                <Tag color="green">{result.summary.零件数}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="齐套组数">
                <Tag color="orange">{result.summary.齐套组数}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="分段数">
                {result.summary.分段数}
              </Descriptions.Item>
              <Descriptions.Item label="套料图数">
                {result.summary.套料图数}
              </Descriptions.Item>
              <Descriptions.Item label="工艺参数">
                {result.speed_table_uploaded
                  ? <Tag color="green">附件3 ✓ ({result.speed_sheet_count}个Sheet)</Tag>
                  : <Tag color="default">默认值</Tag>
                }
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card title="🔍 校验明细" size="small">
            {Object.entries(result.validation).map(([key, val]) => {
              const isIssueField = issueKeys.includes(key);
              const isIssue = isIssueField && Number(val) > 0;
              return (
                <div
                  key={key}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '4px 0',
                    borderBottom: `1px solid ${COLORS.divider}`,
                  }}
                >
                  <Text>{key}</Text>
                  <Space>
                    {isIssue ? (
                      <WarningOutlined style={{ color: COLORS.warning }} />
                    ) : (
                      <CheckCircleOutlined style={{ color: COLORS.success }} />
                    )}
                    <Text strong={isIssue} type={isIssue ? 'warning' : undefined}>
                      {String(val)}
                    </Text>
                  </Space>
                </div>
              );
            })}
          </Card>
        </>
      )}
    </div>
  );
};

export default DataImport;
