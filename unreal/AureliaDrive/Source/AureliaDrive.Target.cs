using UnrealBuildTool;

public class AureliaDriveTarget : TargetRules
{
	public AureliaDriveTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("AureliaDrive");
	}
}
