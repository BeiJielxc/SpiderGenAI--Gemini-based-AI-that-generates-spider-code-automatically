import React, { useRef, useCallback } from 'react';
import { Image, Paperclip, X, Clipboard } from 'lucide-react';
import { Attachment } from '../types';

interface RichInputProps {
  label: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onFileSelect: (files: Attachment[]) => void;
  attachedFiles: Attachment[];
  onRemoveFile: (index: number) => void;
  name: string;
  placeholder?: string;
  className?: string;
}

// 将文件转换为 base64
const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => {
      const result = reader.result as string;
      // 移除 "data:image/png;base64," 前缀
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = error => reject(error);
  });
};

const RichInput: React.FC<RichInputProps> = ({
  label,
  value,
  onChange,
  onFileSelect,
  attachedFiles,
  onRemoveFile,
  name,
  placeholder,
  className = ''
}) => {
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 处理文件选择（转换为带 base64 的 Attachment）
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const files = Array.from(e.target.files);
      const attachments: Attachment[] = await Promise.all(
        files.map(async (file) => {
          // 只对图片进行 base64 编码
          if (file.type.startsWith('image/')) {
            const base64 = await fileToBase64(file);
            return { file, base64, mimeType: file.type };
          }
          return { file, mimeType: file.type };
        })
      );
      onFileSelect(attachments);
    }
    e.target.value = '';
  };

  // 处理粘贴事件（支持从剪贴板粘贴图片）
  const handlePaste = useCallback(async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: Attachment[] = [];
    
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith('image/')) {
        e.preventDefault(); // 阻止默认粘贴行为
        const file = item.getAsFile();
        if (file) {
          const base64 = await fileToBase64(file);
          // 生成一个有意义的文件名
          const timestamp = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
          const ext = file.type.split('/')[1] || 'png';
          const newFile = new File([file], `screenshot_${timestamp}.${ext}`, { type: file.type });
          imageFiles.push({ file: newFile, base64, mimeType: file.type });
        }
      }
    }

    if (imageFiles.length > 0) {
      onFileSelect(imageFiles);
    }
  }, [onFileSelect]);

  return (
    <div className={`flex flex-col gap-1.5 col-span-2 ${className}`}>
      <label className="text-sm font-medium text-gray-700 ml-1">
        {label}
      </label>
      <div className="
        w-full bg-white text-gray-800 border border-gray-200 rounded-xl
        focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-100
        transition-all duration-200 shadow-sm hover:border-gray-300
        overflow-hidden flex flex-col
      ">
        <textarea
          ref={textareaRef}
          name={name}
          value={value}
          onChange={onChange}
          onPaste={handlePaste}
          placeholder={placeholder || "请输入内容，支持直接粘贴截图 (Ctrl+V)..."}
          className="w-full p-4 min-h-[100px] outline-none resize-y placeholder:text-gray-300 text-sm leading-relaxed bg-white"
        />
        
        {/* Attachments Preview List */}
        {attachedFiles.length > 0 && (
          <div className="px-4 pb-2 flex flex-wrap gap-2 bg-white">
            {attachedFiles.map((attachment, index) => (
              <div 
                key={index} 
                className="flex items-center gap-1.5 bg-gray-50 border border-gray-200 text-xs pl-2 pr-1 py-1 rounded-md text-gray-600 animate-in fade-in slide-in-from-bottom-1"
              >
                {/* 图片预览缩略图 */}
                {attachment.base64 && attachment.mimeType?.startsWith('image/') && (
                  <img 
                    src={`data:${attachment.mimeType};base64,${attachment.base64}`}
                    alt="预览"
                    className="w-8 h-8 object-cover rounded border border-gray-200"
                  />
                )}
                <span className="max-w-[150px] truncate font-medium">{attachment.file.name}</span>
                <button 
                  type="button" 
                  onClick={() => onRemoveFile(index)}
                  className="p-0.5 hover:bg-gray-200 rounded text-gray-400 hover:text-red-500 transition-colors"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Action Toolbar */}
        <div className="px-3 py-2 bg-white border-t border-gray-100 flex gap-2">
          <button
            type="button"
            onClick={() => imageInputRef.current?.click()}
            className="
              px-3 py-1.5 rounded-lg text-gray-600 hover:text-emerald-700 hover:bg-gray-50 
              transition-all duration-200 flex items-center gap-2 text-xs font-medium border border-transparent hover:border-emerald-100
            "
          >
            <Image size={16} />
            <span>添加图片</span>
          </button>
          <input 
            type="file" 
            ref={imageInputRef} 
            className="hidden" 
            accept="image/*"
            multiple 
            onChange={handleFileChange}
          />

          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="
              px-3 py-1.5 rounded-lg text-gray-600 hover:text-emerald-700 hover:bg-gray-50 
              transition-all duration-200 flex items-center gap-2 text-xs font-medium border border-transparent hover:border-emerald-100
            "
          >
            <Paperclip size={16} />
            <span>附加文件</span>
          </button>
          <input 
            type="file" 
            ref={fileInputRef} 
            className="hidden" 
            multiple
            onChange={handleFileChange}
          />
        </div>
      </div>
    </div>
  );
};

export default RichInput;