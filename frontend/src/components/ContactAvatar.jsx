import { useEffect, useState } from "react";
import { UserCircle } from "lucide-react";

export default function ContactAvatar({
  name,
  photoUrl,
  size = "md",
  className = "",
}) {
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    setHasError(false);
  }, [photoUrl]);

  const sizeClasses = {
    sm: "h-6 w-6",
    md: "h-10 w-10",
    lg: "h-11 w-11",
  };

  const iconSizes = {
    sm: 14,
    md: 20,
    lg: 22,
  };

  const resolvedSize = sizeClasses[size] || sizeClasses.md;
  const resolvedIcon = iconSizes[size] || iconSizes.md;

  if (photoUrl && !hasError) {
    return (
      <img
        src={photoUrl}
        alt={name || "Contato"}
        onError={() => setHasError(true)}
        className={`${resolvedSize} rounded-full object-cover ${className}`}
      />
    );
  }

  return (
    <div
      className={`flex ${resolvedSize} items-center justify-center rounded-full bg-gray-200 text-gray-600 ${className}`}
    >
      <UserCircle size={resolvedIcon} />
    </div>
  );
}
